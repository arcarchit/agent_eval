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
