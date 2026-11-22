// Statistics. Pure functions, no I/O, unit tested in stats.test.ts.
//
// The unit of analysis is the TASK: for each task and config we average the N repeated trials
// into a pass rate. Comparing A vs B we take per-task differences d_t = rateA_t - rateB_t
// (paired: same tasks) and bootstrap over tasks to get a 95% CI of the mean difference.
// Resampling tasks (not trials) is the honest choice: trials of the same task are correlated.

export interface TrialRow {
  task_id: string;
  config_id: string;
  status: string;
  passed: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  failure_category: string | null;
  llm_calls?: number | null;
  turns?: number | null;
}

/** Small seeded RNG so results are reproducible. */
export function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);

/** Mean of one resample (with replacement) of xs. */
function resampleMean(xs: number[], rand: () => number): number {
  let s = 0;
  for (let i = 0; i < xs.length; i++) s += xs[Math.floor(rand() * xs.length)];
  return s / xs.length;
}

/** Bootstrap distribution of the mean: resample with replacement B times. Sorted. */
export function bootstrapMeans(xs: number[], B = 5000, seed = 1): number[] {
  const rand = rng(seed);
  const out: number[] = [];
  for (let b = 0; b < B; b++) out.push(resampleMean(xs, rand));
  return out.sort((a, b) => a - b);
}

/** Bootstrap distribution of mean(xa) - mean(xb) for two independent samples. Sorted. */
export function bootstrapDiff(xa: number[], xb: number[], B = 2000, seed = 1): number[] {
  const rand = rng(seed);
  const out: number[] = [];
  for (let b = 0; b < B; b++) out.push(resampleMean(xa, rand) - resampleMean(xb, rand));
  return out.sort((a, b) => a - b);
}

export function percentileCI(sorted: number[], level = 0.95): [number, number] {
  const lo = sorted[Math.floor(((1 - level) / 2) * sorted.length)];
  const hi = sorted[Math.min(sorted.length - 1, Math.floor(((1 + level) / 2) * sorted.length))];
  return [lo, hi];
}

/** Pass rate per task for one config, ignoring infrastructure errors. */
export function taskRates(trials: TrialRow[], configId: string): Map<string, number[]> {
  const byTask = new Map<string, number[]>();
  for (const t of trials) {
    if (t.config_id !== configId || t.status !== 'done') continue;
    if (!byTask.has(t.task_id)) byTask.set(t.task_id, []);
    byTask.get(t.task_id)!.push(t.passed ? 1 : 0);
  }
  return byTask;
}

export function summarize(trials: TrialRow[], configId: string) {
  const mine = trials.filter((t) => t.config_id === configId);
  const done = mine.filter((t) => t.status === 'done');
  const rates = [...taskRates(trials, configId).values()].map(mean);
  const failures: Record<string, number> = {};
  for (const t of done) if (t.failure_category) failures[t.failure_category] = (failures[t.failure_category] ?? 0) + 1;
  const latencies = done.map((t) => t.latency_ms ?? 0).sort((a, b) => a - b);
  return {
    config_id: configId,
    trials_done: done.length,
    trials_error: mine.filter((t) => t.status === 'error').length,
    trials_pending: mine.filter((t) => t.status === 'pending' || t.status === 'running').length,
    trials_cancelled: mine.filter((t) => t.status === 'cancelled').length,
    pass_rate: mean(rates),
    pass_rate_ci: rates.length ? percentileCI(bootstrapMeans(rates)) : [NaN, NaN],
    avg_cost_usd: mean(done.map((t) => t.cost_usd ?? 0)),
    total_cost_usd: done.reduce((s, t) => s + (t.cost_usd ?? 0), 0),
    avg_latency_ms: mean(latencies),
    p90_latency_ms: latencies[Math.floor(latencies.length * 0.9)] ?? NaN,
    avg_tokens: mean(done.map((t) => (t.input_tokens ?? 0) + (t.output_tokens ?? 0))),
    avg_llm_calls: mean(done.map((t) => t.llm_calls ?? 0)),   // model calls per trial
    avg_turns: mean(done.map((t) => t.turns ?? 0)),           // user turns (sessions) per trial
    failures,
  };
}

export type Verdict = 'A better' | 'B better' | 'no significant difference';

export function compare(trials: TrialRow[], a: string, b: string) {
  const ra = taskRates(trials, a);
  const rb = taskRates(trials, b);
  const tasks = [...ra.keys()].filter((t) => rb.has(t)).sort(); // paired: only tasks both configs finished
  const diffs = tasks.map((t) => mean(ra.get(t)!) - mean(rb.get(t)!));
  const boot = bootstrapMeans(diffs);
  const ci = diffs.length ? percentileCI(boot) : ([NaN, NaN] as [number, number]);
  // Two-sided bootstrap p-value: how often the resampled difference lands on the other side of 0.
  const below = boot.filter((x) => x <= 0).length / boot.length;
  const above = boot.filter((x) => x >= 0).length / boot.length;
  const verdict: Verdict = ci[0] > 0 ? 'A better' : ci[1] < 0 ? 'B better' : 'no significant difference';

  const perTask = tasks.map((task, i) => {
    const xa = ra.get(task)!;
    const xb = rb.get(task)!;
    // Per-task CI: resample A's trials and B's trials independently.
    const tci = percentileCI(bootstrapDiff(xa, xb));
    return {
      task_id: task,
      a_rate: mean(xa), b_rate: mean(xb), n_a: xa.length, n_b: xb.length,
      delta: diffs[i], ci: tci,
      label: tci[0] > 0 ? 'A better' : tci[1] < 0 ? 'B better' : diffs[i] === 0 ? 'same' : 'noise',
    };
  });

  return {
    a, b,
    tasks_compared: tasks.length,
    tasks_dropped: [...new Set([...ra.keys(), ...rb.keys()])].filter((t) => !tasks.includes(t)),
    mean_diff: mean(diffs),
    ci,
    p_value: diffs.length ? Math.min(1, 2 * Math.min(below, above)) : NaN, // no paired tasks: no test
    verdict,
    per_task: perTask.sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta)),
    summary_a: summarize(trials, a),
    summary_b: summarize(trials, b),
  };
}

/** One cell per (task, config): pass counts and per-trial averages over finished trials. Cancelled trials are left out. */
export function taskMatrix(trials: TrialRow[]) {
  const groups: Record<string, Record<string, TrialRow[]>> = {};
  for (const t of trials) {
    if (t.status === 'cancelled') continue;
    ((groups[t.task_id] ??= {})[t.config_id] ??= []).push(t);
  }
  const matrix: Record<string, Record<string, Record<string, number>>> = {};
  for (const [task, byConfig] of Object.entries(groups)) {
    for (const [config, rows] of Object.entries(byConfig)) {
      const done = rows.filter((t) => t.status === 'done');
      (matrix[task] ??= {})[config] = {
        passed: done.filter((t) => t.passed).length,
        done: done.length,
        error: rows.filter((t) => t.status === 'error').length,
        pending: rows.filter((t) => t.status === 'pending' || t.status === 'running').length,
        avg_latency_ms: mean(done.map((t) => t.latency_ms ?? 0)),
        avg_cost_usd: mean(done.map((t) => t.cost_usd ?? 0)),
        avg_llm_calls: mean(done.map((t) => t.llm_calls ?? 0)),
        avg_turns: mean(done.map((t) => t.turns ?? 0)),
      };
    }
  }
  return matrix;
}
