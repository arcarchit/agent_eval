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
| `exact` | 10 | the prompt asks for `{"answer": ...}`; the grader takes the last JSON `answer` field (falling back to the whole reply), then trims, lower-cases, and strips quotes and a trailing period |
| `regex` | 2 | same answer extraction; used when several formats are valid (`$72`, `72.00`) |
| `file_check` | 5 | checks the workspace after the run: number, exact text, or JSON equality |
| `unit_test` | 6 | the hidden test file is written **after** the agent finishes, then run with Python |
| `llm_judge` | 3 | rubric where every point must hold, temperature 0, reasoning before the verdict, JSON output, **majority of 5 votes** (all votes stored) |

**Why JSON answers**: in the first real run, correct answers wrapped in a sentence ("The trip is 215 minutes.") failed exact match. That measured formatting, not the skill. Asking for `{"answer": ...}` and extracting the field keeps grading strict but fair. A reply that ignores the format and is not a bare answer still fails.

**Deterministic graders** are covered by unit tests (`worker/tests`). Every task's expected answer was checked
by hand or with a script (for example the weekday of 2024-02-29). Ambiguous tasks were rewritten: an earlier
`file-02` had a line `DEBUG ERROR_COUNT=2`, so "lines containing ERROR" could be 3 or 4.

**Judge validation**: `judge_validation/labels.json` has 19 answers (8 pass, 11 fail) for the three judge tasks.
They include subtle failures: "So sorry!" where "sorry" is banned, a missing CI-check fix, 5 sentences when
the limit is 3. `worker/judge_validation.py` reports accuracy, Cohen's kappa ([how it is computed](STATISTICS.md#judge-agreement-cohens-kappa)), false passes and false fails.
It also reports **instability**: examples where repeated calls on the same input disagreed.
**Acceptance rule**: kappa ≥ 0.8, at most 1 false pass, and 0 unstable items. Otherwise fix the rubric before
trusting judge tasks. See `docs/JUDGE_VALIDATION.md` for a real instability found in the first experiment
(the same answer got 3 passes and 4 fails), what was changed, and the latest validation: gpt-4o-mini reaches
kappa 0.68, below the bar, so the 3 judge tasks are reported separately until literal rules move to deterministic graders.

## 6. Statistical method

A step-by-step explanation with a worked example from a real experiment is in [STATISTICS.md](STATISTICS.md).

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

**Choosing N: by simulation, not by rule of thumb** (`python worker/power_analysis.py`, about 6 s, no API calls).

There are two sources of noise:
1. **Trial noise** inside a task: an agent is random, so N trials give a noisy pass rate (SD up to 0.5/√N).
2. **Task noise**: tasks differ, and we only have a sample of them.

N only reduces (1). The number of tasks reduces (2).

The script runs *our exact method* (per-task means, paired differences, bootstrap CI over tasks) on 250 fake
experiments per cell, where the true answer is known, with 19 tasks and realistic base pass rates (many tasks
always pass or always fail). Each cell is the probability that the verdict is "A better":

| Truth | N=1 | N=3 | N=5 | N=10 |
|-------|-----|-----|-----|------|
| No difference (**false positive rate**; should be ~5%) | 2% | 5% | 6% | 6% |
| Skills-like: 5 tasks go from 10% to 90% (mean +0.21) | 30% | 58% | **73%** | **88%** |
| +20 points on every task (mean +0.13 after capping at 100%) | 26% | 71% | 84% | 96% |
| +10 points on every task (mean +0.07) | 8% | 29% | 39% | 64% |

(Each number is ±3 points of simulation noise.)

What this tells us:
- **The method is calibrated**: with no real difference it declares a winner about 5% of the time, as a 95% CI should.
  Treating trials as independent would not be calibrated like this.
- **N=1 is useless** for agents: even a large effect is missed 70% of the time.
- **N=5 detects the skills effect 73% of the time; N=10 detects it 88% of the time.** The usual target is 80%, so for a final
  decision N=10 is the right choice. It doubles the cost (≈ $0.34 instead of $0.17 on gpt-4o-mini; see USAGE_AND_COST.md).
  N=5 is the minimum for iterating.
- **Small effects need more tasks, not more trials.** Even at N=10, a +10-point improvement is found only 64% of the
  time with 19 tasks. Past N≈10, adding tasks is what increases power.
- The simulation is slightly pessimistic for temperature 0: real trial outcomes for a task are more consistent than
  independent coin flips, so real trial noise is lower.

**Decision rule we use:** N=5 while iterating (cheap, catches large effects); N=10 for the reported experiment. A
"no significant difference" verdict means "not detected at this power", not "proven equal". The power table
tells you how large an effect could have been missed.

## 7. Failure analysis (`worker/failures.py`)

Every failed trial gets exactly one category. Rules run over the trace in this order; the first match wins.
This table is the reference; the UI shows the same text as a tooltip (`FAILURE_HELP` in `ui/src/api.ts`).

| Category | Rule | What it usually means |
|----------|------|-----------------------|
| `timeout` | trial exceeded the config's `timeout_s` | agent or tool hung, or a very slow model |
| `provider_error` | `stop_reason == "error"` | safety net only: API failures normally end as status `error` and are excluded |
| `step_exhaustion` | hit `max_steps` | going in circles, or the budget is too small for the task |
| `loop` | the harness's loop guard stopped it | Hermes detected a repeating tool loop |
| `truncated` | a reply hit `max_tokens` and the turn ended | a config limit: raise `max_tokens`, or the model rambles / thinks too long |
| `hallucinated_tool` | called a tool name that was not offered | invented a tool, or a malformed call (e.g. the JSON arguments put in the name field) |
| `bad_tool_args` | tool arguments were not valid JSON | weak tool-calling format |
| `tool_errors` | at least half (and ≥ 2) of the tool calls returned errors | wrong paths, wrong arguments |
| `loop` | the same call (name + arguments) 3+ times | stuck, without the harness noticing |
| `gave_up` | empty final answer | stopped without answering |
| `wrong_answer` | none of the above | finished normally; the grader failed the answer |

The UI shows the counts per config, so a regression points to a cause. For example, "skills off →
wrong_answer on skill-*" means the model guessed the format.

Precedence matters: a trial that both hallucinated a tool and gave a wrong answer is counted once, as
`hallucinated_tool`. Treat the categories as a first pointer and confirm by opening the trajectory.

Not implemented: "grader disagreement" (it would need two graders per task). It is listed as future work.

## 8. Known limits

The full, maintained list is in [FEATURES.md](FEATURES.md#known-limitations). The ones that matter most for
*trusting a conclusion*:
- a task set of this size detects only large effects (see the power table in §6: a +7-point average gain is found only 64% of the time even at N=10);
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
