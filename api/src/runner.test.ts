import { describe, expect, it } from 'vitest';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ApiController } from './api.controller';
import { openDb } from './db';
import { Runner } from './runner.service';

process.env.FAKE_STATE_DIR = mkdtempSync(join(tmpdir(), 'fake-'));
process.env.WORKER_CMD = `node ${join(__dirname, '../test/fake-worker.js')}`;
process.env.BACKOFF_MS = '10';

describe('runner + api', () => {
  it('runs an experiment: pass, fail, retry on rate limit, timeout, crash', async () => {
    const db = openDb(':memory:');
    const runner = new Runner(db);
    const api = new ApiController(db, runner);

    const cfg: any = api.createConfig({ name: 'test', harness: 'baseline', model: 'gpt-4o-mini', timeout_s: 1 });
    expect(cfg.id).toBe('test@v1');
    // same settings -> same version; new settings -> v2
    expect((api.createConfig({ name: 'test', harness: 'baseline', model: 'gpt-4o-mini', timeout_s: 1 }) as any).id).toBe('test@v1');
    expect((api.createConfig({ name: 'test', harness: 'baseline', model: 'gpt-4o-mini', timeout_s: 2 }) as any).id).toBe('test@v2');
    expect(() => api.createConfig({ name: 'x', harness: 'nope', model: 'gpt-4o-mini' })).toThrow();

    const exp = api.createExperiment({
      name: 'e', config_ids: ['test@v1'], trials_per_task: 1,
      task_ids: ['arith-01', 'arith-02', 'logic-01', 'text-01', 'date-01'],
    });
    await runner.idle();

    const trials = api.listTrials(exp.id);
    const by = (task: string) => trials.find((t) => t.task_id === task)!;
    expect(by('arith-01')).toMatchObject({ status: 'done', passed: 1 });
    expect(by('arith-02')).toMatchObject({ status: 'done', passed: 0, failure_category: 'wrong_answer' });
    expect(by('logic-01')).toMatchObject({ status: 'done', passed: 1, attempts: 2 });        // retried
    expect(by('text-01')).toMatchObject({ status: 'done', passed: 0, failure_category: 'timeout' });
    expect(by('date-01')).toMatchObject({ status: 'error' });                                // excluded from stats

    const e = api.getExperiment(exp.id);
    expect(e.status).toBe('done');
    expect(e.summaries[0]).toMatchObject({ trials_done: 4, trials_error: 1 });
  }, 20_000);

  it('config stores a snapshot of the model entry', () => {
    const db = openDb(':memory:');
    const api = new ApiController(db, new Runner(db));
    const cfg: any = api.createConfig({ name: 'm', harness: 'baseline', model: 'gpt-4o-mini' });
    expect(JSON.parse(cfg.spec).model_spec.provider_model).toBe('openai/gpt-4o-mini');
  });

  it('cancel stops pending trials; running ones finish', async () => {
    const db = openDb(':memory:');
    const runner = new Runner(db);
    const api = new ApiController(db, runner);
    api.createConfig({ name: 'c', harness: 'baseline', model: 'gpt-4o-mini' });
    const exp = api.createExperiment({ name: 'e', config_ids: ['c@v1'], trials_per_task: 10, task_ids: ['arith-01'] });
    const { cancelled } = api.cancel(exp.id, {});
    await runner.idle();
    const statuses = api.listTrials(exp.id).map((t) => t.status);
    expect(cancelled).toBe(6);                                    // 4 were already running (MAX_CONCURRENCY)
    expect(statuses.filter((s) => s === 'done').length).toBe(4);
    expect(api.getExperiment(exp.id).status).toBe('done');
  }, 20_000);

  it('circuit breaker cancels a config whose first trials all error', async () => {
    const db = openDb(':memory:');
    const runner = new Runner(db);
    const api = new ApiController(db, runner);
    api.createConfig({ name: 'bad', harness: 'baseline', model: 'gpt-4o-mini' });
    const exp = api.createExperiment({ name: 'e', config_ids: ['bad@v1'], trials_per_task: 12, task_ids: ['date-01'] });
    await runner.idle();
    const s = api.getExperiment(exp.id).summaries[0];
    expect(s.trials_done).toBe(0);
    expect(s.trials_cancelled).toBeGreaterThan(0);                // did not run all 12
    expect(s.trials_error + s.trials_cancelled).toBe(12);
  }, 20_000);

  it('adds a fixed config to an existing experiment, reusing finished trials', async () => {
    const db = openDb(':memory:');
    const runner = new Runner(db);
    const api = new ApiController(db, runner);
    api.createConfig({ name: 'good', harness: 'baseline', model: 'gpt-4o-mini' });
    api.createConfig({ name: 'fixed', harness: 'baseline', model: 'gpt-4o-mini', temperature: 0.5 });
    const exp = api.createExperiment({ name: 'e', config_ids: ['good@v1'], trials_per_task: 2, task_ids: ['arith-01', 'arith-02'] });
    await runner.idle();
    const before = api.listTrials(exp.id);
    expect(api.addConfig(exp.id, { config_id: 'fixed@v1' }).added).toBe(4);
    await runner.idle();
    const after = api.listTrials(exp.id);
    expect(after.filter((t) => t.config_id === 'fixed@v1' && t.status === 'done').length).toBe(4);
    for (const t of before) expect(after.find((x) => x.id === t.id)).toMatchObject({ status: t.status, passed: t.passed });
    const cmp = api.compare(exp.id, 'good@v1', 'fixed@v1');
    expect(cmp.tasks_compared).toBe(2);
    expect(cmp.differences).toEqual([{ field: 'temperature', a: 0, b: 0.5 }]);   // exactly one setting differs
    expect(() => api.addConfig(exp.id, { config_id: 'fixed@v1' })).toThrow();
  }, 20_000);

  it('resumes trials left running by a crash', () => {
    const db = openDb(':memory:');
    db.exec(`INSERT INTO trials (id, experiment_id, config_id, task_id, trial_index, status) VALUES ('x', 'e', 'c', 't', 0, 'running')`);
    const runner = new Runner(db);
    (runner as any).pump = () => {}; // do not actually run
    runner.onModuleInit();
    expect((db.prepare(`SELECT status FROM trials WHERE id = 'x'`).get() as any).status).toBe('pending');
    expect((runner as any).queue).toEqual(['x']);
  });
});
