// Executes trials. Each trial = one Python process (worker/run_trial.py).
//
// Reliability rules:
//  - at most MAX_CONCURRENCY trials run at once (protects rate limits)
//  - a trial that exceeds its timeout is killed and scored as a failure ("timeout")
//  - exit code 2 (rate limit / provider outage) is retried with exponential backoff
//  - after MAX_ATTEMPTS, or on a crash, the trial is marked "error": it is EXCLUDED from the
//    statistics (not counted as a fail) and shown separately, so outages do not corrupt results
//  - a trial cancelled while queued is skipped; a running one finishes (killing it would waste the spend)
//  - every trial result is written in one UPDATE, and on startup "running" trials go back to
//    "pending" and resume, so a crash of the API never leaves half-written results
import { Injectable, OnModuleInit } from '@nestjs/common';
import { spawn } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import { now } from './db';

const MAX_CONCURRENCY = Number(process.env.MAX_CONCURRENCY ?? 4);
const MAX_ATTEMPTS = Number(process.env.MAX_ATTEMPTS ?? 4);
const BACKOFF_MS = Number(process.env.BACKOFF_MS ?? 2000);
// Circuit breaker: if a config's first N finished trials are ALL errors (wrong model id, bad key,
// no credits), cancel the rest of that config instead of burning through hundreds of failing trials.
const BREAKER_ERRORS = Number(process.env.BREAKER_ERRORS ?? 3);
const workerCmd = () => (process.env.WORKER_CMD ?? 'python3 ../worker/run_trial.py').split(' ');

export interface WorkerResult {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

/** Spawn the worker, send the job on stdin, collect stdout. Kill it after timeoutMs. */
export function runWorker(job: object, timeoutMs: number, cmd = workerCmd()): Promise<WorkerResult> {
  return new Promise((resolve) => {
    const child = spawn(cmd[0], cmd.slice(1), { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, timeoutMs);
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr = (stderr + d).slice(-5000)));
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ code, stdout, stderr, timedOut });
    });
    child.stdin.end(JSON.stringify(job));
  });
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

@Injectable()
export class Runner implements OnModuleInit {
  private queue: string[] = [];
  private active = 0;

  constructor(private readonly db: DatabaseSync) {}

  onModuleInit() {
    // Resume after a restart.
    this.db.exec(`UPDATE trials SET status = 'pending' WHERE status = 'running'`);
    const rows = this.db.prepare(`SELECT id FROM trials WHERE status = 'pending' ORDER BY id`).all() as { id: string }[];
    this.enqueue(rows.map((r) => r.id));
  }

  enqueue(trialIds: string[]) {
    this.queue.push(...trialIds);
    this.pump();
  }

  /** Resolves when nothing is queued or running (used by tests). */
  async idle() {
    while (this.queue.length || this.active) await sleep(20);
  }

  private pump() {
    while (this.active < MAX_CONCURRENCY && this.queue.length) {
      const id = this.queue.shift()!;
      this.active++;
      this.runTrial(id)
        .catch((e) => this.finish(id, { status: 'error', error: String(e) }))
        .finally(() => {
          this.active--;
          this.pump();
        });
    }
  }

  private async runTrial(id: string) {
    const trial = this.db.prepare(`SELECT * FROM trials WHERE id = ?`).get(id) as any;
    if (trial.status !== 'pending') return; // cancelled while it waited in the queue
    const exp = this.db.prepare(`SELECT * FROM experiments WHERE id = ?`).get(trial.experiment_id) as any;
    const config = this.db.prepare(`SELECT * FROM configs WHERE id = ?`).get(trial.config_id) as any;
    const task = JSON.parse(exp.tasks).find((t: any) => t.id === trial.task_id);
    const spec = JSON.parse(config.spec);
    const timeoutMs = (spec.timeout_s ?? 300) * 1000;

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      this.db.prepare(`UPDATE trials SET status = 'running', attempts = ? WHERE id = ?`).run(attempt, id);
      const res = await runWorker({ task, config: spec }, timeoutMs);

      if (res.timedOut) {
        return this.finish(id, { status: 'done', passed: 0, score: 0, failure_category: 'timeout',
          error: `killed after ${spec.timeout_s ?? 300}s`, latency_ms: timeoutMs });
      }
      const out = parseLastJsonLine(res.stdout);
      if (res.code === 0 && out?.status === 'ok') {
        return this.finish(id, {
          status: 'done', passed: out.passed ? 1 : 0, score: out.score, failure_category: out.failure_category,
          grader: JSON.stringify(out.grader), trace: JSON.stringify(out.trace),
          llm_calls: out.llm_calls ?? null, turns: out.turns ?? null,   // older workers do not send these
          input_tokens: out.input_tokens, output_tokens: out.output_tokens, cost_usd: out.cost_usd,
          judge_cost_usd: out.judge_cost_usd, latency_ms: out.latency_ms,
        });
      }
      const error = out?.error ?? res.stderr.slice(-2000) ?? `exit ${res.code}`;
      if (res.code === 2 && attempt < MAX_ATTEMPTS) {
        await sleep(BACKOFF_MS * 2 ** (attempt - 1) * (1 + Math.random() * 0.25)); // backoff + jitter
        continue;
      }
      return this.finish(id, { status: 'error', error });
    }
  }

  private finish(id: string, fields: Record<string, unknown>) {
    const cols = Object.keys(fields);
    this.db
      .prepare(`UPDATE trials SET ${cols.map((c) => `${c} = ?`).join(', ')}, finished_at = ? WHERE id = ?`)
      .run(...(Object.values(fields) as any[]), now(), id);
    const trial = this.db.prepare(`SELECT experiment_id, config_id FROM trials WHERE id = ?`).get(id) as any;
    if (fields.status === 'error') this.maybeTripBreaker(trial.experiment_id, trial.config_id);
    this.markDoneIfFinished(trial.experiment_id);
  }

  /** An experiment is done when none of its trials are pending or running. */
  markDoneIfFinished(experimentId: string) {
    this.db.prepare(`UPDATE experiments SET status = 'done' WHERE id = ? AND NOT EXISTS
                     (SELECT 1 FROM trials WHERE experiment_id = ? AND status IN ('pending','running'))`)
      .run(experimentId, experimentId);
  }

  private maybeTripBreaker(experimentId: string, configId: string) {
    const c = this.db.prepare(`SELECT SUM(status = 'error') AS errors, SUM(status = 'done') AS done
      FROM trials WHERE experiment_id = ? AND config_id = ?`).get(experimentId, configId) as any;
    if (c.done === 0 && c.errors >= BREAKER_ERRORS) {
      this.db.prepare(`UPDATE trials SET status = 'cancelled', error = 'circuit breaker: first trials of this config all errored'
        WHERE experiment_id = ? AND config_id = ? AND status = 'pending'`).run(experimentId, configId);
    }
  }
}

function parseLastJsonLine(stdout: string): any {
  const line = stdout.trim().split('\n').pop();
  try {
    return line ? JSON.parse(line) : null;
  } catch {
    return null;
  }
}
