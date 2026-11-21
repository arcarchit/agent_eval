# Design

## 1. Architecture

```
┌────────────┐  HTTP   ┌──────────────────────────────┐  spawn (stdin: job JSON)   ┌──────────────────────────┐
│ Vue 3 UI   │ ──────▶ │ NestJS API                   │ ─────────────────────────▶ │ Python worker            │
│ configs    │         │ api.controller.ts  routes    │                            │ run_trial.py             │
│ experiments│         │ runner.service.ts  pool,     │ ◀───────────────────────── │  harness adapter         │
│ compare    │         │   retries, timeouts, resume  │  stdout: result JSON       │  → grader → failure rule │
│ trial view │         │ stats.ts           bootstrap │  exit 0 ok / 2 retry / 1   │  (one process per trial) │
└────────────┘         │ SQLite (node:sqlite)         │                            └──────────────────────────┘
                       └──────────────────────────────┘
```

**Boundary**: TypeScript owns everything that is *platform* (state, scheduling, statistics, UI).
Python owns everything that is *agent* (harnesses, graders), because Hermes is a Python library.
The two meet at one contract: a JSON job in on stdin, a JSON result out on stdout, and an exit code.

**Why a process per trial**: it is the simplest way to get isolation. Each trial has its own memory,
its own temp workspace and its own `HERMES_HOME`. A hang is killed with SIGKILL at the timeout, and a
crash cannot affect other trials. The cost is ~1–2 s of Python start-up per trial, which is small
next to LLM latency.

## 2. Adapter interfaces

**Harness** (`worker/harnesses/base.py`):

```python
class Harness(ABC):
    def run(self, task, config, model, workspace, home) -> Trace
```

- `Trace` (`worker/common.py`) is the common output: a list of events (`session_start`, `user`, `assistant`,
  `tool_call`, `tool_result`, `memory_read`, `memory_write`, `skill_used`), plus `final_answer`, `stop_reason`,
  `steps` and token counts.
- **Hermes adapter** (`hermes.py`): sets `HERMES_HOME` to the trial's temp dir, copies in only the enabled skills,
  enables toolsets from the config (`file`, `terminal`, plus `memory` / `skills` when on), calls
  `AIAgent.run_conversation()`, and converts Hermes' OpenAI-style message list into trace events. A Hermes
  `memory` tool call with add/replace/remove becomes a `memory_write`. The `MEMORY.md` that Hermes injects at
  session start is recorded as a `memory_read`. `skill_view` becomes `skill_used`.
- **Baseline adapter** (`baseline.py`): ~100-line hand-written tool loop with the same capabilities
  (file tools, shell, memory notes, `read_skill`). It proves the interface is not Hermes-shaped.

**Model**: every model is an entry in `models.yaml` (OpenAI-compatible `base_url`, model id, key env var, price).
Both harnesses take a `Model` object, so a new model needs no code. A provider without an OpenAI-compatible
API would need a small proxy such as LiteLLM. This is an assumption, not code in this repo.

## 3. Data model (SQLite, `api/src/db.ts`)

| Table | Key fields | Notes |
|-------|-----------|-------|
| `configs` | `id = name@vN`, `spec` (JSON), `spec_hash` | **Immutable.** Saving new settings under the same name creates `vN+1`. The same settings return the existing version. The spec includes a **snapshot of the model's `models.yaml` entry** (`model_spec`), so editing `models.yaml` never changes what an existing config runs. |
| `experiments` | `config_ids`, `tasks` (JSON snapshot), `trials_per_task` | The full task definitions are copied in, so editing a task file later does not change a past experiment. |
| `trials` | `(experiment, config, task, trial_index)` unique, `status` (pending/running/done/error/cancelled), `attempts`, `passed`, `score`, `failure_category`, `grader` (JSON), `trace` (JSON), tokens, cost, latency, `error` | One row per run. Written in a single UPDATE when finished. |

Tasks, skills and models are files in git, so they are versioned by git. Hermes is pinned to a commit in the Dockerfile.

## 4. Isolation and reproducibility

- **Isolation**: new process, new temp `workspace/` (task setup files only), new temp `home/` (memory and skills).
  All of it is deleted after the trial. Trials share nothing. The only cross-session state is memory
  *inside* a trial, and only when the config enables it.
- **File access**: the baseline file tools refuse paths outside the workspace. Hermes runs with its working
  directory and `TERMINAL_CWD` set to the workspace.
- **Reproduce**: `POST /api/experiments/:id/rerun` re-runs the same config versions on the same task snapshot.
  LLM sampling is not deterministic, so a rerun reproduces the *setup*, and the statistics tell you whether
  the new numbers agree with the old ones within their CIs.
- **Failure handling**: concurrency limit (`MAX_CONCURRENCY`); exit code 2 (rate limit or provider 5xx) is retried
  with exponential backoff and jitter, up to 4 attempts; a non-retryable provider failure (HTTP 402 out of
  credits, 401 bad key) ends the trial as `error` immediately and is **never graded**; a timeout kills the process and scores the trial as a
  failure (`timeout`); a crash or exhausted retries gives `status = error`, which is **excluded** from statistics
  and shown as its own count. On API restart, `running` trials go back to `pending` and resume.
- **Stopping and reuse**: `POST /experiments/:id/cancel` (optionally for one config) marks pending trials `cancelled`;
  running ones finish. A **circuit breaker** cancels a config automatically when its first 3 finished trials are all
  errors (wrong model id, bad key, no credits). `POST /experiments/:id/configs` adds a config to an existing
  experiment on the same task snapshot and N, so a fixed config can be compared against data that already exists.
  Caveat: the two configs then ran at different times, so provider-side drift between the runs is not controlled.

## 5. Graders and how they were validated

| Grader | Tasks | Notes |
|--------|-------|-------|
| `exact` | 9 | the prompt asks for `{"answer": ...}`; the grader takes the last JSON `answer` field (falling back to the whole reply), then trims, lower-cases, and strips quotes and a trailing period |
| `regex` | 2 | same answer extraction; used when several formats are valid (`$72`, `72.00`) |
| `file_check` | 3 | checks the workspace after the run: number, exact text, or JSON equality |
| `unit_test` | 4 | the hidden test file is written **after** the agent finishes, then run with Python |
| `llm_judge` | 3 | rubric where every point must hold, temperature 0, reasoning before the verdict, JSON output, **majority of 5 votes** (all votes stored) |

**Why JSON answers**: in the first real run, correct answers wrapped in a sentence ("The trip is 215 minutes.") failed exact match. That measured formatting, not the skill. Asking for `{"answer": ...}` and extracting the field keeps grading strict but fair. A reply that ignores the format and is not a bare answer still fails.

**Deterministic graders** are covered by unit tests (`worker/tests`). Every task's expected answer was checked
by hand or with a script (for example the weekday of 2024-02-29). Ambiguous tasks were rewritten: an earlier
`file-02` had a line `DEBUG ERROR_COUNT=2`, so "lines containing ERROR" could be 3 or 4.

**Judge validation**: `judge_validation/labels.json` has 18 answers (7 pass, 11 fail) for the three judge tasks.
They include subtle failures: "So sorry!" where "sorry" is banned, a missing CI-check fix, 5 sentences when
the limit is 3. `worker/judge_validation.py` reports accuracy, Cohen's kappa, false passes and false fails.
It also reports **instability**: examples where repeated calls on the same input disagreed.
**Acceptance rule**: kappa ≥ 0.8, at most 1 false pass, and 0 unstable items. Otherwise fix the rubric before
trusting judge tasks. See `docs/internal/JUDGE_VALIDATION.md` for a real instability found in the first experiment
(the same answer got 3 passes and 4 fails) and how it was fixed.

## 6. Statistical method

- **Unit of analysis = task.** For each config and task, average the N trials into a pass rate. Trials of the same
  task are correlated, so treating 21×5 trials as 105 independent samples would make the CIs falsely narrow.
- **Per-config pass rate**: the mean of the task pass rates, with a 95% bootstrap CI that resamples tasks
  (5,000 resamples, seeded, so the same data always gives the same CI).
- **A vs B (paired)**: `d_t = rateA_t − rateB_t` for every task both configs finished. A 95% bootstrap CI of
  `mean(d_t)` is computed by resampling tasks.
  - CI entirely above 0 → **A better**. Entirely below 0 → **B better**. Contains 0 → **no significant
    difference**, and the UI says the difference may be noise.
  - A two-sided bootstrap p-value is shown as well, as a secondary number.
- **Per-task deltas**: each task gets its own delta and a CI from resampling A's and B's trials independently.
  With N=5 these CIs are wide on purpose: a single task rarely proves anything, but the table shows *where* the
  aggregate difference comes from.
- **Why bootstrap and not McNemar**: McNemar needs one binary outcome per pair. With N trials per task there is
  no natural pairing between trial *i* of A and trial *i* of B. The paired-by-task bootstrap uses all trials,
  needs no normality assumption, and is easy to explain.
- **Cost, latency, tokens**: mean cost per trial, total cost, mean and p90 latency, mean tokens, shown next to the
  pass rate for every config.

**Choosing N.** Two sources of noise: (1) trial noise inside a task, with SD ≤ 0.5/√N for a pass rate (N=5 → ≤0.22);
(2) differences between tasks. The CI half-width of the mean difference is about `2·SD(d_t)/√n_tasks`. With 21
tasks and SD(d_t) ≈ 0.3, that is ≈ ±0.13. So this task set can detect an effect of about 13 points or more. A
targeted effect such as skills (5 tasks going from ~0% to ~80%) moves the mean by ~19 points, which is detectable.
Raising N above 5 mainly shrinks (1). Past that, more **tasks** help more than more trials. N=5 is the point
where trial noise (≤0.22 per task, averaged over 21 tasks) is smaller than the between-task spread, at a cost
of a few cents for this experiment.

## 7. Failure analysis (`worker/failures.py`)

Rules over the trace, first match wins: `timeout`, `provider_error` (safety net; such trials should already be
`error`), `step_exhaustion` (hit `max_steps`), `loop` when the harness's own loop guard stopped it, `hallucinated_tool`
(called a tool not offered), `bad_tool_args` (arguments not valid JSON), `tool_errors` (most tool calls
failed), `loop` (same call 3+ times), `gave_up` (empty final answer), `wrong_answer`. The UI shows the counts
per config, so a regression points to a cause. For example, "skills off → wrong_answer on skill-*" means the
model guessed the format.

Not implemented: "grader disagreement" (it would need two graders per task). It is listed as future work.

## 8. Known limits

The full, maintained list is in [FEATURES.md](FEATURES.md#known-limitations). The ones that matter most for
*trusting a conclusion*:
- 21 tasks can detect only large effects (about 13 points or more);
- the judge is an LLM with possible bias and run-to-run instability (mitigated by explicit rubrics, a 5-vote majority and validation);
- the sandbox is not a security boundary (an agent with the terminal tool could read the task files);
- provider errors are excluded rather than imputed;
- comparisons built by adding a config later span different times.

## 9. Cost

Formula, measured numbers (calls, tokens and turns per trial, judge calls, the judge validation script) and a worked
example are in [USAGE_AND_COST.md](USAGE_AND_COST.md). Summary:

```
cost = tasks × N × configs × (avg_input_tokens × price_in + avg_output_tokens × price_out)
     + judge_tasks × N × configs × judge_votes × judge_tokens × judge_price
```
Measured for Hermes + gpt-4o-mini: about 2.5–3 calls and ~4–6k input / ~120 output tokens per trial,
**≈ $0.0007–0.0009 per trial**. The 190-trial skills experiment is **≈ $0.17** including 150 judge calls.
(An earlier estimate in this doc of ~12k input tokens per trial was too high; the measured Hermes prompt is
~1.7k tokens per call.)

**How to reduce it:** prompt caching, early stopping (N=2 first, then add trials only where configs disagree), task
sampling for quick checks, a result cache for identical trials, deterministic graders where possible, and fewer
judge votes once the judge is shown to be stable. Details in [USAGE_AND_COST.md](USAGE_AND_COST.md#ways-to-reduce-calls-and-cost).
