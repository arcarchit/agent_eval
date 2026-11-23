import { describe, expect, it } from 'vitest';
import { bootstrapMeans, compare, compareByCategory, mean, percentileCI, taskMatrix, TrialRow } from './stats';

const row = (task: string, config: string, passed: boolean, status = 'done'): TrialRow => ({
  task_id: task, config_id: config, status, passed: passed ? 1 : 0,
  cost_usd: 0.01, latency_ms: 100, input_tokens: 10, output_tokens: 5, failure_category: passed ? null : 'wrong_answer',
});

/** n tasks, N trials; A passes with probability pa (deterministic pattern), B with pb. */
function make(n: number, N: number, aPasses: (t: number, i: number) => boolean, bPasses: (t: number, i: number) => boolean) {
  const rows: TrialRow[] = [];
  for (let t = 0; t < n; t++)
    for (let i = 0; i < N; i++) rows.push(row(`t${t}`, 'A', aPasses(t, i)), row(`t${t}`, 'B', bPasses(t, i)));
  return rows;
}

describe('stats', () => {
  it('bootstrap CI contains the mean and is reproducible', () => {
    const xs = [0, 1, 1, 0, 1, 1, 1, 0, 1, 1];
    const ci = percentileCI(bootstrapMeans(xs));
    expect(ci[0]).toBeLessThan(mean(xs));
    expect(ci[1]).toBeGreaterThan(mean(xs));
    expect(percentileCI(bootstrapMeans(xs))).toEqual(ci);
  });

  it('identical configs -> no significant difference', () => {
    const r = compare(make(20, 5, (t, i) => (t + i) % 2 === 0, (t, i) => (t + i) % 2 === 0), 'A', 'B');
    expect(r.mean_diff).toBe(0);
    expect(r.verdict).toBe('no significant difference');
  });

  it('A clearly better on many tasks -> A better, CI above 0', () => {
    const r = compare(make(20, 5, () => true, (t) => t % 3 === 0), 'A', 'B');
    expect(r.verdict).toBe('A better');
    expect(r.ci[0]).toBeGreaterThan(0);
    expect(r.p_value).toBeLessThan(0.05);
  });

  it('one lucky task out of 20 is called noise', () => {
    const r = compare(make(20, 3, (t, i) => t === 0 && i < 2, () => false), 'A', 'B');
    expect(r.verdict).toBe('no significant difference');
  });

  it('errored trials are excluded, and tasks missing on one side are dropped from pairing', () => {
    const rows = [row('t1', 'A', true), row('t1', 'B', false), row('t2', 'A', true), row('t2', 'B', false, 'error')];
    const r = compare(rows, 'A', 'B');
    expect(r.tasks_compared).toBe(1);
    expect(r.tasks_dropped).toEqual(['t2']);
    expect(r.summary_b.trials_error).toBe(1);
  });

  it('task matrix: counts and per-trial averages over finished trials only', () => {
    const rows: TrialRow[] = [
      { ...row('t1', 'A', true), latency_ms: 100, llm_calls: 2, turns: 1 },
      { ...row('t1', 'A', false), latency_ms: 300, llm_calls: 4, turns: 1 },
      row('t1', 'A', false, 'error'),
      row('t1', 'A', false, 'cancelled'),
    ];
    expect(taskMatrix(rows).t1.A).toMatchObject({ passed: 1, done: 2, error: 1, pending: 0, avg_latency_ms: 200, avg_llm_calls: 3, avg_turns: 1 });
  });

  it('per-category comparison: winner only with enough tasks, "too few tasks" otherwise', () => {
    // 6 coding tasks where A always passes and B never does; 2 math tasks where B wins; 2 identical text tasks.
    const rows = [
      ...make(6, 3, () => true, () => false),
      ...['m0', 'm1'].flatMap((t) => [row(t, 'A', false), row(t, 'B', true)]),
      ...['x0', 'x1'].flatMap((t) => [row(t, 'A', true), row(t, 'B', true)]),
    ];
    const cat: Record<string, string> = { m0: 'math', m1: 'math', x0: 'text', x1: 'text' };
    for (let t = 0; t < 6; t++) cat[`t${t}`] = 'coding';
    const by = Object.fromEntries(compareByCategory(rows, 'A', 'B', cat).map((c) => [c.category, c]));
    expect(by.coding).toMatchObject({ tasks: 6, a_rate: 1, b_rate: 0, delta: 1, label: 'A better' });
    expect(by.math).toMatchObject({ tasks: 2, delta: -1, label: 'too few tasks' });
    expect(by.text).toMatchObject({ tasks: 2, delta: 0, label: 'same' });
  });
});
