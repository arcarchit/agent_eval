# How the numbers on an experiment page are computed

This page explains every statistic on the experiment page, with a worked example from a real run, and how
the LLM judge is checked with Cohen's kappa. The
short version of the method, and how N was chosen, is in [DESIGN.md §6](DESIGN.md#6-statistical-method).
The code is `api/src/stats.ts` (pure functions, unit-tested in `stats.test.ts`).

## The one idea: the task is the unit

An experiment runs every task N times under every config. Trials of the same task are strongly correlated:
when a task is hard, all its trials tend to fail. If we treated 26 tasks × 5 trials as 130 independent coin
flips, the confidence intervals would come out far too narrow, and we would call noise a win.

So every statistic first collapses the N trials of a task into one number: that task's **pass rate**
(e.g. 1 pass out of 5 = 0.2). Everything after that works on the 26 task rates, not the 130 trials.

Only trials with status `done` count. Infrastructure errors (rate limits, no credits, crashes) and
cancelled trials are left out, not counted as failures.

## Worked example

Experiment `33037e1d`: baseline harness, file + terminal tools, both skills, 26 tasks, N=5.

- **A** = `b-l-s-ft@v1`: Llama 3.3 70B
- **B** = `b-m-s-ft@v1`: Ministral 8B

### 1. Pass rate per config, with a 95% CI

1. Pass rate = mean of the 26 task pass rates.
2. Bootstrap: draw 26 tasks **with replacement** from those 26 rates (some tasks appear twice, some not at
   all) and take the mean. Repeat 5,000 times.
3. Sort the 5,000 means; the 95% CI is the 2.5th and 97.5th percentile.

| Config | Pass rate | 95% CI |
|--------|-----------|--------|
| A (Llama 70B) | 28.5% | 13.1% – 45.4% |
| B (Ministral 8B) | 35.4% | 20.0% – 53.1% |

The bootstrap answers: *if we had picked a different set of 26 similar tasks, how much could this number
move?* With only 26 tasks, the answer is "a lot", which is why the intervals are wide.

### 2. A vs B: the verdict

1. **Pair by task.** For every task both configs finished, compute `d = rate_A − rate_B`. Pairing removes
   task difficulty from the comparison: a hard task is hard for both configs.
2. **Bootstrap the mean of d** over tasks, 5,000 times, and take the 95% CI.
3. **Verdict:** CI entirely above 0 → *A better*; entirely below 0 → *B better*; contains 0 → *no
   significant difference* (the page says the difference may be noise).
4. **p-value** (secondary): the share of bootstrap means on the far side of 0, doubled (two-sided).

Result: mean difference **−6.9 points**, 95% CI **[−23.1, +9.2]**, p = 0.41 → **no significant
difference**. Ministral scored a bit higher, but a gap this size is well within what chance produces with
26 tasks.

"No significant difference" means *not detected at this power*, not *proven equal*. The power table in
DESIGN.md §6 shows how large an effect could have been missed.

### 3. Per-task deltas

Each task row shows A's rate, B's rate, the delta, and a CI from resampling A's N trials and B's N trials
independently (2,000 times). The label is *A better*, *B better*, *same* (identical rates) or *noise*.

| Task | A | B | Per-task CI | Label |
|------|---|---|-------------|-------|
| code-01 | 0/5 | 5/5 | [−1.00, −1.00] | B better |
| skill-01 | 5/5 | 0/5 | [+1.00, +1.00] | A better |
| arith-01 | 5/5 | 1/5 | [+0.40, +1.00] | A better |

Use this table to see **where** the aggregate difference comes from, not to make claims about single tasks.

**Caveat:** when all N trials of each side agree (0/5 vs 5/5), every resample is identical, so the CI
collapses to a single point and looks certain. With N=5 that certainty is not real: 5 out of 5 is
compatible with a true pass rate as low as about 50%. Only the aggregate verdict in step 2 is a tested claim.

### 4. Per-category comparison

Every task has a `category` (coding, reasoning, hard, skill, files, writing, memory). The table "By task
category" runs the same paired bootstrap as step 2, restricted to one category's tasks, and shows cost and
latency per trial next to it. Categories come from the experiment's task snapshot.

| Category | Tasks | A (Llama) | B (Ministral) | Δ | 95% CI | Reading | Cost / trial A · B |
|----------|-------|-----------|---------------|---|--------|---------|--------------------|
| coding | 4 | 10% | 75% | −65 | [−100, −25] | too few tasks | $0.00013 · $0.00020 |
| files | 3 | 67% | 67% | 0 | – | same | $0.00014 · $0.00015 |
| hard | 5 | 0% | 28% | −28 | [−52, −4] | B better | $0.00024 · $0.00094 |
| memory | 2 | 0% | 0% | 0 | – | same | $0.00017 · $0.00028 |
| reasoning | 5 | 60% | 44% | +16 | [0, +48] | noise | $0.00011 · $0.00012 |
| skill | 5 | 32% | 8% | +24 | [0, +64] | noise | $0.00039 · $0.00020 |
| writing | 2 | 20% | 10% | +10 | [0, +20] | too few tasks | $0.00012 · $0.00010 |

Read it with two cautions:
- **Small categories.** Below 5 tasks the row is labelled *too few tasks* and no winner is called: with 2
  tasks there are only 3 possible resample means, so the CI means little. Even 5 tasks is the bare minimum.
- **Many comparisons.** Seven categories, each tested at 95%, give roughly a 1-in-3 chance that at least one
  shows a "winner" by luck. So "B better on hard tasks" is a lead, not a finding: here the trajectories
  support it (Llama pastes code into its answer instead of writing files), and more hard tasks would confirm it.

### 5. Cost, latency and tokens

Shown next to quality for every config so trade-offs are visible: mean and total cost, mean and p90
latency, mean tokens (input + output), mean LLM calls and user turns per trial. These are plain averages
over `done` trials, without CIs.

## Judge agreement: Cohen's kappa

This is not on the experiment page; it is how `worker/judge_validation.py` checks the LLM judge against 19
hand-labelled answers (`judge_validation/labels.json`). Code: `cohens_kappa()` in that file.

**Why not just accuracy?** Accuracy rewards guessing the common label. 11 of the 19 labels are *fail*, so a
"judge" that always says fail would score 11/19 = 58% accuracy while judging nothing. Kappa measures agreement
**beyond what chance would give**: 1 = perfect, 0 = no better than chance, below 0 = worse than chance.

**Formula:** `kappa = (p_o − p_e) / (1 − p_e)`
- `p_o` = observed agreement (accuracy).
- `p_e` = agreement expected by chance if judge and human labelled independently with their own pass rates:
  `p_e = P(judge pass) × P(human pass) + P(judge fail) × P(human fail)`.

**Worked example:** gpt-4o-mini, 1 vote (`judge_validation/results/report_gpt-4o-mini_v1.json`).

|                 | Human: pass | Human: fail | Total |
|-----------------|-------------|-------------|-------|
| **Judge: pass** | 7           | 2 (false pass) | 9  |
| **Judge: fail** | 1 (false fail) | 9        | 10    |
| **Total**       | 8           | 11          | 19    |

1. `p_o` = (7 + 9) / 19 = **0.842** (the 84.2% accuracy).
2. Judge says pass 9/19 = 0.474; human says pass 8/19 = 0.421.
   `p_e` = 0.474 × 0.421 + 0.526 × 0.579 = 0.199 + 0.305 = **0.504**.
   Half of the agreement would happen by chance alone.
3. `kappa` = (0.842 − 0.504) / (1 − 0.504) = 0.338 / 0.496 = **0.68**.

So the judge removes 68% of the disagreement that chance would leave. The acceptance bar is 0.8 (a common
reading: 0.6–0.8 "substantial", above 0.8 "almost perfect"), so this judge does not pass.

**Read kappa together with the error direction.** Kappa treats both mistakes alike. For a grader, false passes
are worse (a broken answer counted as success inflates a config's score), so the acceptance rule also caps them
at 1, and separately requires 0 unstable items (repeated calls that disagree).

**Caveat:** with 19 items kappa is itself noisy: one changed verdict moves it by about 0.1. It is a
screening check, not a precise measurement.

## Why a bootstrap and not McNemar

McNemar's test needs one pass/fail pair per item. With N trials per task there is no natural pairing
between trial *i* of A and trial *i* of B, so we would have to throw away N−1 trials or pair arbitrarily.
The paired-by-task bootstrap uses every trial, needs no normality assumption, and is easy to explain.

## Reproducibility

The random number generator is seeded, so the same trials always give the same CIs and verdict. The
simulation in `worker/power_analysis.py` runs this exact method on fake experiments where the truth is
known: with no real difference it declares a winner about 5% of the time, as a 95% CI should.
