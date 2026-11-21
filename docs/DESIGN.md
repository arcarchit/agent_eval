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
| `configs` | `id = name@vN`, `spec` (JSON), `spec_hash` | **Immutable.** Saving new settings under the same name creates `vN+1`. The same settings return the existing version. |
| `experiments` | `config_ids`, `tasks` (JSON snapshot), `trials_per_task` | The full task definitions are copied in, so editing a task file later does not change a past experiment. |
| `trials` | `(experiment, config, task, trial_index)` unique, `status`, `attempts`, `passed`, `score`, `failure_category`, `grader` (JSON), `trace` (JSON), tokens, cost, latency, `error` | One row per run. Written in a single UPDATE when finished. |

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
trusting judge tasks. See `docs/JUDGE_VALIDATION.md` for a real instability found in the first experiment
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

## 8. Known limits: where not to trust the conclusions

- **Task-set size**: 21 tasks, ~5 per category. Only large effects are detectable (see N above). Do not read
  meaning into per-category differences of one or two tasks.
- **Synthetic, easy tasks**: most are short. Hard multi-step agentic work (long refactors, web research) is not
  covered, and results may not carry over to it.
- **Judge bias**: the judge is an LLM, possibly from the same family as the agent being judged (self-preference),
  and it may favour longer answers. It is checked against only 18 labels written by the author, not independent
  annotators. Only 3 tasks use it.
- **Contamination**: generic tasks (palindromes, fizzbuzz-style, sky colour) are surely in training data. The
  ACME skill tasks use made-up rules for exactly this reason.
- **Harness quirks**: Hermes adds a large system prompt (skills index, memory, identity) and its own retries and
  context compression, so its token counts and latency are not directly comparable with the minimal baseline.
  The baseline `memory_read` events are explicit tool calls, while Hermes reads memory through prompt injection.
  Hermes sees the skill index in its system prompt; the baseline sees skill names in its system prompt.
- **Grader secrecy**: the harness gets the task without its grader (`agent_view()` in `run_trial.py`), and hidden
  unit tests are written only after the agent finishes. Filesystem access to `/app/tasks` is the remaining gap (below).
- **Sandbox**: a temp dir plus a process plus a timeout is **not** a security boundary. Inside Docker your Mac is
  safe, but Hermes' `terminal` and file tools can still reach the container: `/app/tasks` (expected answers, so
  an agent could cheat), the SQLite DB, and environment variables including the API key. Use a container or
  microVM per trial, with no secrets and no task files mounted, before running untrusted tasks or treating
  scores as cheat-proof.
- **Hermes can edit skills** (`skill_manage` comes with the `skills` toolset). Edits go to the trial's private
  copy and are thrown away, so they never reach the repo, other trials or the baseline. But *within* a trial,
  Hermes can use a skill as a notepad: in the first run it saved a memory-task fact as a skill with memory off.
  So skills-on can leak into memory tasks; exclude `mem-*` from skills experiments.
- **Provider errors** (rate limits after retries, out of credits) are excluded rather than imputed. If errors cluster on one config, the comparison can be
  biased; the UI shows error counts so this is visible.
- **Prices** are static numbers in `models.yaml`. Provider-side caching and discounts are ignored.
- **Single machine**: the in-process queue is not distributed. A crash loses only in-flight trials, which re-run
  on restart.

## 9. Cost

**Formula** (per experiment):

```
cost = tasks × trials × configs × (avg_input_tokens × price_in + avg_output_tokens × price_out)
     + judge_tasks × trials × configs × judge_tokens × judge_price
```

Rough numbers for Hermes + gpt-4o-mini: about 3 model calls per trial with Hermes' ~4k-token system prompt and
tool schemas → ~12k input + ~300 output tokens per trial →
`12,000 × $0.15/M + 300 × $0.60/M ≈ $0.002` per trial. For 21 tasks × 5 trials × 2 configs = 210 trials,
that is **≈ $0.40**, and judge calls add < $0.01. The actual spend is shown on the experiment page (total cost
per config) and is recorded in `docs/EXPERIMENT.md`.

**How to reduce it**:
- **Prompt caching**: Hermes' system prompt is identical across trials, and provider prefix caching cuts input
  cost by 50–90%.
- **Early stopping (sequential testing)**: run N=2 first, and add trials only for tasks where the configs
  disagree. Tasks where both configs score 0/2 or 2/2 rarely change the verdict.
- **Sampling**: run a stratified subset of tasks for quick checks, and the full set before a release.
- **Cache identical trials**: at temperature 0 with a fixed seed, re-use results for (config hash, task hash) pairs.
- **Cheap judge**: deterministic graders where possible (18/21 tasks here), and a small judge model, validated
  against labels.
