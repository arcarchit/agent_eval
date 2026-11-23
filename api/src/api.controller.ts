// All HTTP routes. Kept in one file so the whole API surface is readable top to bottom.
import { BadRequestException, Body, Controller, Get, NotFoundException, Param, Post, Query, Res } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import type { Response } from 'express';
import { HARNESSES, TOOLSETS, loadModelSpecs, loadModels, loadSkills, loadTasks } from './catalog';
import { now } from './db';
import { Runner } from './runner.service';
import { compare, summarize, taskMatrix, TrialRow } from './stats';

/** Build a config spec with a fixed key order, so the same settings always hash the same. */
export function normalizeSpec(body: any) {
  const spec = {
    harness: String(body.harness),
    model: String(body.model),
    skills: [...(body.skills ?? [])].sort(),
    memory: Boolean(body.memory),
    tools: [...(body.tools ?? ['file'])].sort(),
    temperature: Number(body.temperature ?? 0),
    max_steps: Number(body.max_steps ?? 10),
    max_tokens: Number(body.max_tokens ?? 1024),
    timeout_s: Number(body.timeout_s ?? 300),
    // Snapshot of the model's models.yaml entry. A config must not change meaning when models.yaml is
    // edited (e.g. fixing a wrong provider_model): the fix gives a different hash, so a new version.
    model_spec: null as null | Record<string, unknown>,
  };
  const m = loadModelSpecs()[spec.model];
  const errors = [];
  if (!HARNESSES.includes(spec.harness)) errors.push(`harness must be one of ${HARNESSES}`);
  if (!m) errors.push(`unknown model ${spec.model}`);
  else spec.model_spec = { provider_model: m.provider_model, base_url: m.base_url, api_key_env: m.api_key_env,
                           price_per_mtok: m.price_per_mtok, provider_routing: m.provider_routing ?? null };
  for (const s of spec.skills) if (!loadSkills().includes(s)) errors.push(`unknown skill ${s}`);
  for (const t of spec.tools) if (!TOOLSETS.includes(t)) errors.push(`unknown toolset ${t}`);
  if (spec.max_steps < 1 || spec.timeout_s < 1) errors.push('max_steps and timeout_s must be >= 1');
  if (errors.length) throw new BadRequestException(errors.join('; '));
  return spec;
}

@Controller('api')
export class ApiController {
  constructor(private readonly db: DatabaseSync, private readonly runner: Runner) {}

  @Get('meta')
  meta() {
    const tasks = loadTasks().map((t) => ({ id: t.id, category: t.category, grader: t.grader.type, needs: t.needs ?? null }));
    return { harnesses: HARNESSES, models: loadModels(), skills: loadSkills(), toolsets: TOOLSETS, tasks };
  }

  // ---------- configs: immutable and versioned ----------
  @Get('configs')
  listConfigs() {
    return this.db.prepare(`SELECT * FROM configs ORDER BY name, version`).all()
      .map((c: any) => ({ ...c, spec: JSON.parse(c.spec) }));
  }

  /** Same name + same settings -> returns the existing version. Same name + new settings -> new version. */
  @Post('configs')
  createConfig(@Body() body: any) {
    const name = String(body.name ?? '').trim();
    if (!/^[\w.-]+$/.test(name)) throw new BadRequestException('name: letters, digits, - _ . only');
    const spec = normalizeSpec(body);
    const hash = createHash('sha256').update(JSON.stringify(spec)).digest('hex').slice(0, 12);
    const same = this.db.prepare(`SELECT * FROM configs WHERE name = ? AND spec_hash = ?`).get(name, hash);
    if (same) return same;
    const last = this.db.prepare(`SELECT MAX(version) AS v FROM configs WHERE name = ?`).get(name) as any;
    const version = (last?.v ?? 0) + 1;
    const id = `${name}@v${version}`;
    this.db.prepare(`INSERT INTO configs VALUES (?, ?, ?, ?, ?, ?)`).run(id, name, version, JSON.stringify(spec), hash, now());
    return this.db.prepare(`SELECT * FROM configs WHERE id = ?`).get(id);
  }

  // ---------- experiments ----------
  @Get('experiments')
  listExperiments() {
    return this.db.prepare(`
      SELECT e.id, e.name, e.config_ids, e.trials_per_task, e.status, e.created_at,
             COUNT(t.id) AS total, SUM(t.status = 'done') AS done, SUM(t.status = 'error') AS errors,
             SUM(t.status = 'cancelled') AS cancelled
      FROM experiments e LEFT JOIN trials t ON t.experiment_id = e.id
      GROUP BY e.id ORDER BY e.created_at DESC`).all()
      .map((e: any) => ({ ...e, config_ids: JSON.parse(e.config_ids) }));
  }

  @Post('experiments')
  createExperiment(@Body() body: any) {
    const configIds: string[] = body.config_ids ?? [];
    if (!configIds.length) throw new BadRequestException('config_ids required');
    configIds.forEach((c) => this.requireConfig(c));
    const n = Number(body.trials_per_task ?? 3);
    if (!(n >= 1 && n <= 50)) throw new BadRequestException('trials_per_task must be 1..50');
    let tasks = loadTasks();
    if (body.task_ids?.length) tasks = tasks.filter((t) => body.task_ids.includes(t.id));
    return this.startExperiment(String(body.name || 'experiment'), configIds, tasks, n);
  }

  /** Reproduce: same config versions, same task snapshot, fresh trials. */
  @Post('experiments/:id/rerun')
  rerun(@Param('id') id: string) {
    const e = this.getExperimentRow(id);
    return this.startExperiment(`${e.name} (rerun)`, JSON.parse(e.config_ids), JSON.parse(e.tasks), e.trials_per_task);
  }

  /** Stop: pending trials become "cancelled" and are never run. Running trials finish normally.
   *  Pass config_id to stop only one config (e.g. it has a wrong model id), or nothing to stop all. */
  @Post('experiments/:id/cancel')
  cancel(@Param('id') id: string, @Body() body: any) {
    this.getExperimentRow(id);
    const where = body?.config_id ? `AND config_id = ?` : '';
    const args = body?.config_id ? [id, body.config_id] : [id];
    const r = this.db.prepare(`UPDATE trials SET status = 'cancelled' WHERE experiment_id = ? AND status = 'pending' ${where}`).run(...args);
    this.runner.markDoneIfFinished(id);
    return { cancelled: Number(r.changes) };
  }

  /** Reuse finished data: run one more config on the SAME task snapshot and N, inside the same experiment.
   *  Typical use: one config failed (wrong model id); fix it as a new config version and add it here,
   *  instead of re-running the config that already worked. */
  @Post('experiments/:id/configs')
  addConfig(@Param('id') id: string, @Body() body: any) {
    const e = this.getExperimentRow(id);
    const configId = String(body?.config_id ?? '');
    this.requireConfig(configId);
    const configIds: string[] = JSON.parse(e.config_ids);
    if (configIds.includes(configId)) throw new BadRequestException(`${configId} is already in this experiment`);
    this.db.exec('BEGIN');
    const ids = this.insertTrials(id, [configId], JSON.parse(e.tasks), e.trials_per_task);
    this.db.prepare(`UPDATE experiments SET config_ids = ?, status = 'running' WHERE id = ?`)
      .run(JSON.stringify([...configIds, configId]), id);
    this.db.exec('COMMIT');
    this.runner.enqueue(ids);
    return { id, added: ids.length };
  }

  private startExperiment(name: string, configIds: string[], tasks: any[], n: number) {
    const id = randomUUID().slice(0, 8);
    this.db.exec('BEGIN');
    this.db.prepare(`INSERT INTO experiments VALUES (?, ?, ?, ?, ?, 'running', ?)`)
      .run(id, name, JSON.stringify(configIds), JSON.stringify(tasks), n, now());
    const ids = this.insertTrials(id, configIds, tasks, n);
    this.db.exec('COMMIT');
    this.runner.enqueue(ids);
    return { id, trials: ids.length };
  }

  /** One pending trial per repeat × task × config. Interleaved (repeat 0 of every task and config first), so a
   *  partial run still covers every config fairly. Trial ids are "<experiment>-<n>", continuing the numbering. */
  private insertTrials(experimentId: string, configIds: string[], tasks: any[], n: number): string[] {
    const insert = this.db.prepare(
      `INSERT INTO trials (id, experiment_id, config_id, task_id, trial_index, status) VALUES (?, ?, ?, ?, ?, 'pending')`);
    let next = (this.db.prepare(`SELECT COUNT(*) AS n FROM trials WHERE experiment_id = ?`).get(experimentId) as any).n;
    const ids: string[] = [];
    for (let i = 0; i < n; i++)
      for (const task of tasks)
        for (const c of configIds) {
          const tid = `${experimentId}-${next++}`;
          insert.run(tid, experimentId, c, task.id, i);
          ids.push(tid);
        }
    return ids;
  }

  @Get('experiments/:id')
  getExperiment(@Param('id') id: string) {
    const e = this.getExperimentRow(id);
    const configIds: string[] = JSON.parse(e.config_ids);
    const trials = this.trialRows(id);
    const configs = configIds.map((c) => ({ id: c, spec: this.configSpec(c) }));
    return {
      id: e.id, name: e.name, status: e.status, created_at: e.created_at, trials_per_task: e.trials_per_task,
      configs, tasks: JSON.parse(e.tasks).map((t: any) => ({ id: t.id, category: t.category, grader: t.grader.type })),
      summaries: configIds.map((c) => summarize(trials, c)),
      matrix: taskMatrix(trials),
    };
  }

  @Get('experiments/:id/compare')
  compare(@Param('id') id: string, @Query('a') a: string, @Query('b') b: string) {
    this.getExperimentRow(id);
    return { ...compare(this.trialRows(id), a, b), differences: this.configDifferences(a, b) };
  }

  /** Which settings differ between two configs. A fair comparison changes exactly one. */
  private configDifferences(a: string, b: string) {
    const sa = this.configSpec(a);
    const sb = this.configSpec(b);
    const fields = [...new Set([...Object.keys(sa), ...Object.keys(sb)])];
    return fields
      // model_spec follows from model; report it only when the same model name hides different details
      .filter((f) => !(f === 'model_spec' && sa.model !== sb.model))
      .filter((f) => JSON.stringify(sa[f]) !== JSON.stringify(sb[f]))
      .map((f) => ({ field: f, a: sa[f] ?? null, b: sb[f] ?? null }));
  }

  @Get('experiments/:id/trials')
  listTrials(@Param('id') id: string, @Query('task') task?: string, @Query('config') config?: string) {
    return this.trialRows(id).filter((t) => (!task || t.task_id === task) && (!config || t.config_id === config));
  }

  @Get('experiments/:id/export')
  export(@Param('id') id: string, @Query('format') format = 'json', @Res() res: Response) {
    const e = this.getExperimentRow(id);
    const trials = this.db.prepare(`SELECT * FROM trials WHERE experiment_id = ? ORDER BY id`).all(id) as any[];
    if (format === 'csv') {
      const cols = ['id', 'config_id', 'task_id', 'trial_index', 'status', 'passed', 'score', 'failure_category',
        'llm_calls', 'turns', 'input_tokens', 'output_tokens', 'cost_usd', 'judge_cost_usd', 'latency_ms', 'attempts', 'error'];
      const esc = (v: unknown) => (v == null ? '' : `"${String(v).replace(/"/g, '""')}"`);
      const csv = [cols.join(','), ...trials.map((t) => cols.map((c) => esc(t[c])).join(','))].join('\n');
      res.attachment(`experiment-${id}.csv`).type('text/csv').send(csv);
      return;
    }
    const configs = JSON.parse(e.config_ids).map((c: string) => this.db.prepare(`SELECT * FROM configs WHERE id = ?`).get(c));
    res.attachment(`experiment-${id}.json`).json({
      experiment: { ...e, tasks: JSON.parse(e.tasks), config_ids: JSON.parse(e.config_ids) },
      configs,
      trials: trials.map((t) => ({ ...t, trace: t.trace && JSON.parse(t.trace), grader: t.grader && JSON.parse(t.grader) })),
    });
  }

  @Get('trials/:id')
  getTrial(@Param('id') id: string) {
    const t = this.db.prepare(`SELECT * FROM trials WHERE id = ?`).get(id) as any;
    if (!t) throw new NotFoundException();
    const e = this.getExperimentRow(t.experiment_id);
    return {
      ...t,
      trace: t.trace && JSON.parse(t.trace),
      grader: t.grader && JSON.parse(t.grader),
      task: JSON.parse(e.tasks).find((x: any) => x.id === t.task_id),
      config: this.configSpec(t.config_id),
    };
  }

  private requireConfig(id: string) {
    if (!this.db.prepare(`SELECT 1 FROM configs WHERE id = ?`).get(id)) throw new BadRequestException(`unknown config ${id}`);
  }

  private configSpec(id: string): any {
    const row = this.db.prepare(`SELECT spec FROM configs WHERE id = ?`).get(id) as any;
    return row ? JSON.parse(row.spec) : {};
  }

  private getExperimentRow(id: string): any {
    const e = this.db.prepare(`SELECT * FROM experiments WHERE id = ?`).get(id);
    if (!e) throw new NotFoundException(`experiment ${id}`);
    return e;
  }

  private trialRows(id: string): (TrialRow & Record<string, any>)[] {
    return this.db.prepare(`
      SELECT id, task_id, config_id, trial_index, status, passed, score, failure_category, attempts,
             input_tokens, output_tokens, cost_usd, latency_ms, llm_calls, turns, error
      FROM trials WHERE experiment_id = ? ORDER BY task_id, config_id, trial_index`).all(id) as any[];
  }
}
