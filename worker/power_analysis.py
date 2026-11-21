"""How many trials per task (N) do we need? A simulation of our own statistical method.

The platform's comparison (api/src/stats.ts) does this:
  1. average the N trials of each task into a pass rate, per config;
  2. take the per-task difference d_t = rateA_t - rateB_t;
  3. bootstrap over tasks: a 95% CI of mean(d_t); "A better" only if the CI is above 0.

This script runs that same procedure many times on fake data where WE KNOW the truth, and counts:
  - false positive rate: the configs are truly identical, but we declare a winner (should be about 5%);
  - power: config A is truly better, and we detect it (we want 80% or more).

Usage:  python worker/power_analysis.py            (about 1 minute; pure Python, no API calls)
"""
import random

TASKS = 19
# Realistic per-task pass rates for config B: many tasks are easy (always pass) or hard (never pass),
# a few are in between. Measured runs look like this.
BASE = [0.0, 0.0, 0.1, 0.2, 0.3, 0.5, 0.5, 0.6, 0.7, 0.8, 0.8, 0.9, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

SCENARIOS = {
    # name: function(task index, base rate) -> config A's true rate
    "no difference (false positives)": lambda i, p: p,
    "skills-like: 5 tasks 0.1 -> 0.9": lambda i, p: 0.9 if i < 5 else p,
    "+20 points on every task": lambda i, p: min(1.0, p + 0.20),
    "+10 points on every task": lambda i, p: min(1.0, p + 0.10),
}


def ci_of_mean_diff(diffs, rand, resamples=600):
    """Percentile bootstrap over tasks, as in stats.ts."""
    n = len(diffs)
    means = sorted(sum(diffs[int(rand.random() * n)] for _ in range(n)) / n for _ in range(resamples))
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def detect_rate(true_a, true_b, n_trials, sims, rand):
    """Fraction of simulated experiments where the verdict is 'A better'."""
    wins = 0
    for _ in range(sims):
        diffs = []
        for pa, pb in zip(true_a, true_b):
            ra = sum(rand.random() < pa for _ in range(n_trials)) / n_trials
            rb = sum(rand.random() < pb for _ in range(n_trials)) / n_trials
            diffs.append(ra - rb)
        lo, hi = ci_of_mean_diff(diffs, rand)
        wins += lo > 0
    return wins / sims


def main(sims=250, ns=(1, 3, 5, 10)):
    rand = random.Random(42)
    base = (BASE * ((TASKS // len(BASE)) + 1))[:TASKS]
    print(f"{TASKS} tasks, {sims} simulated experiments per cell. Cell = P(verdict 'A better').\n")
    print(f"{'scenario':38}" + "".join(f"  N={n:<4}" for n in ns))
    for name, effect in SCENARIOS.items():
        true_a = [effect(i, p) for i, p in enumerate(base)]
        true_diff = sum(a - b for a, b in zip(true_a, base)) / TASKS
        row = [detect_rate(true_a, base, n, sims, rand) for n in ns]
        print(f"{name:38}" + "".join(f"  {r:5.0%} " for r in row) + f"   (true mean diff {true_diff:+.2f})")


if __name__ == "__main__":
    main()
