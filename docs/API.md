# REST API reference

Base URL: `http://localhost:3000/api`. All requests and responses are JSON. There is no authentication
(see [FEATURES.md](FEATURES.md#known-limitations)). The UI uses only these endpoints, so anything it does can
be scripted.

Errors use NestJS's standard shape:
```json
{ "message": "harness must be one of hermes,baseline", "error": "Bad Request", "statusCode": 400 }
```
`400` = invalid input, `404` = unknown experiment or trial.

| Method | Path | Purpose |
|--------|------|---------|
| GET | [`/meta`](#get-meta) | Harnesses, models, skills, toolsets and tasks available |
| GET | [`/configs`](#get-configs) | List configs (all versions) |
| POST | [`/configs`](#post-configs) | Create a config (or get the existing identical version) |
| GET | [`/experiments`](#get-experiments) | List experiments with progress counts |
| POST | [`/experiments`](#post-experiments) | Start an experiment |
| GET | [`/experiments/:id`](#get-experimentsid) | Status, per-config summaries, per-task matrix |
| GET | [`/experiments/:id/compare?a=&b=`](#get-experimentsidcompare) | A-vs-B with CI, verdict and per-task deltas |
| GET | [`/experiments/:id/trials`](#get-experimentsidtrials) | Trials (no traces), filterable |
| GET | [`/experiments/:id/export?format=`](#get-experimentsidexport) | Download all results as JSON or CSV |
| POST | [`/experiments/:id/cancel`](#post-experimentsidcancel) | Stop pending trials (all or one config) |
| POST | [`/experiments/:id/configs`](#post-experimentsidconfigs) | Add a config to an existing experiment |
| POST | [`/experiments/:id/rerun`](#post-experimentsidrerun) | Re-run with the same config versions and task snapshot |
| GET | [`/trials/:id`](#get-trialsid) | One trial with its full trace and grade |

## Concepts

- **Config**: harness × model × settings. **Immutable**: its id is `name@vN`. Saving different settings under the
  same name creates `vN+1`; saving identical settings returns the existing version.
- **Experiment**: configs × tasks × N trials per task. It stores a **snapshot of the tasks**, so later edits to
  task files do not change it.
- **Trial**: one run of one task under one config. Status: `pending` → `running` → `done` | `error`, or `cancelled`.
  - `done`: the agent finished and was graded, pass or fail. Timeouts count here as a failure.
  - `error`: an infrastructure problem (out of credits, bad model id, rate limit after retries, crash). Excluded from statistics.
  - `cancelled`: stopped before it ran. Excluded from statistics.

---

## GET /meta
```json
{
  "harnesses": ["hermes", "baseline"],
  "models": ["gpt-4o-mini", "llama-3.3-70b", "gemma-4-31b-it-free"],
  "skills": ["acme-billing", "acme-style"],
  "toolsets": ["file", "terminal"],
  "tasks": [{ "id": "arith-01", "category": "reasoning", "grader": "exact", "needs": null }]
}
```
Models, skills and tasks are read from files on every call, so edits show up without a restart.

## GET /configs
An array of configs (same shape as the `POST /configs` response), ordered by name and version.

## POST /configs
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `name` | string | required | letters, digits, `-`, `_`, `.` |
| `harness` | string | required | one of `/meta` harnesses |
| `model` | string | required | a key of `models.yaml` |
| `skills` | string[] | `[]` | skill folder names |
| `memory` | bool | `false` | memory across sessions *within* a trial |
| `tools` | string[] | `["file"]` | `file`, `terminal` |
| `temperature` | number | `0` | |
| `max_steps` | int | `10` | model calls per session |
| `max_tokens` | int | `1024` | per model reply |
| `timeout_s` | int | `300` | per trial; exceeding it is a failure (`timeout`) |

```bash
curl -X POST localhost:3000/api/configs -H 'Content-Type: application/json' \
  -d '{"name":"hermes-skills","harness":"hermes","model":"gpt-4o-mini","skills":["acme-billing"]}'
```
```json
{
  "id": "hermes-skills@v1", "name": "hermes-skills", "version": 1,
  "spec": "{\"harness\":\"hermes\",\"model\":\"gpt-4o-mini\",\"skills\":[\"acme-billing\"],\"memory\":false,\"tools\":[\"file\"],\"temperature\":0,\"max_steps\":10,\"max_tokens\":1024,\"timeout_s\":300,\"model_spec\":{\"provider_model\":\"openai/gpt-4o-mini\",\"base_url\":\"https://openrouter.ai/api/v1\",\"api_key_env\":\"OPENROUTER_API_KEY\",\"price_per_mtok\":{\"input\":0.15,\"output\":0.6}}}",
  "spec_hash": "0c05b323ac9a", "created_at": "2026-11-21T11:31:23.382Z"
}
```
`spec` is a JSON string. `model_spec` is a snapshot of the model's `models.yaml` entry, taken when the config is saved, so editing
`models.yaml` later never changes what this config runs. (The `GET /experiments/:id` response returns `spec` as parsed JSON.)

## GET /experiments
```json
[{ "id": "fc34649f", "name": "demo", "config_ids": ["base@v1", "base@v2"], "trials_per_task": 2,
   "status": "done", "created_at": "...", "total": 8, "done": 8, "errors": 0, "cancelled": 0 }]
```

## POST /experiments
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `name` | string | `"experiment"` | |
| `config_ids` | string[] | required | e.g. `["a@v1","b@v1"]` |
| `trials_per_task` | int | `3` | N, 1–50 |
| `task_ids` | string[] | all tasks | subset of `/meta` tasks |

Returns `{ "id": "fc34649f", "trials": 8 }` immediately. Trials run in the background, at most
`MAX_CONCURRENCY` at a time.

## GET /experiments/:id
```json
{
  "id": "fc34649f", "name": "demo", "status": "running", "trials_per_task": 2,
  "configs": [{ "id": "base@v1", "spec": { "harness": "baseline", "model": "...", "...": "..." } }],
  "tasks": [{ "id": "arith-01", "category": "reasoning", "grader": "exact" }],
  "summaries": [{
    "config_id": "base@v1", "trials_done": 4, "trials_error": 0, "trials_pending": 0, "trials_cancelled": 0,
    "pass_rate": 0.5, "pass_rate_ci": [0.25, 0.75],
    "avg_cost_usd": 0.00012, "total_cost_usd": 0.00048, "avg_latency_ms": 525, "p90_latency_ms": 630,
    "avg_tokens": 110, "failures": { "wrong_answer": 2 }
  }],
  "matrix": { "arith-01": { "base@v1": { "passed": 1, "done": 2, "error": 0, "pending": 0 } } }
}
```
- `pass_rate` is the mean over tasks of each task's pass rate. `pass_rate_ci` is a 95% bootstrap CI over tasks.
- Values that cannot be computed yet (no finished trials) are `null`.

## GET /experiments/:id/compare
Query: `a`, `b` = config ids in this experiment.
```json
{
  "a": "base@v1", "b": "base@v2",
  "tasks_compared": 19, "tasks_dropped": [],
  "mean_diff": 0.16, "ci": [0.05, 0.27], "p_value": 0.004,
  "verdict": "A better",
  "per_task": [{ "task_id": "skill-01", "a_rate": 1, "b_rate": 0, "n_a": 5, "n_b": 5,
                 "delta": 1, "ci": [1, 1], "label": "A better" }],
  "summary_a": { "...": "same shape as summaries[]" }, "summary_b": { "...": "..." }
}
```
- `verdict`: `A better` / `B better` when the 95% CI of the mean per-task difference excludes 0; otherwise
  `no significant difference`.
- `per_task[].label`: `A better`, `B better`, `same` or `noise`. `per_task` is sorted by `|delta|`.
- `tasks_dropped`: tasks without finished trials on both sides; they are not paired.
- `p_value`: two-sided bootstrap p-value, or `null` when no tasks could be paired.
- `differences`: the config settings that differ between A and B, e.g. `[{"field": "model", "a": "llama-3.1-8b", "b": "ministral-8b"}]`.
  A fair single-axis comparison has exactly one entry. `model_spec` is listed only if two configs share a model name
  but its details (id, routing, price) differ.

## GET /experiments/:id/trials
Query (optional): `task`, `config`.
```json
[{ "id": "fc34649f-0", "task_id": "arith-01", "config_id": "base@v1", "trial_index": 0, "status": "done",
   "passed": 0, "score": 0, "failure_category": "wrong_answer", "attempts": 1,
   "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.00012, "latency_ms": 492, "error": null }]
```
`failure_category` is one of: `timeout`, `provider_error`, `step_exhaustion`, `loop`, `truncated`, `hallucinated_tool`,
`bad_tool_args`, `tool_errors`, `gave_up`, `wrong_answer` (or `null` when the trial passed). Definitions:
[DESIGN.md §7](DESIGN.md#7-failure-analysis-workerfailurespy).

## GET /experiments/:id/export
Query: `format=json` (default) or `format=csv`. Downloaded as a file.
- **JSON**: `{ experiment (with the task snapshot), configs, trials (with full traces and grader output) }`.
- **CSV**: one row per trial: `id, config_id, task_id, trial_index, status, passed, score, failure_category,
  input_tokens, output_tokens, cost_usd, judge_cost_usd, latency_ms, attempts, error` (no traces).

## POST /experiments/:id/cancel
Body: `{}` to stop all configs, or `{ "config_id": "b@v1" }` to stop one. Pending trials become `cancelled`;
running trials finish. Returns `{ "cancelled": 206 }`.
The runner also cancels a config by itself when its first 3 finished trials are all `error`
(circuit breaker, `BREAKER_ERRORS`).

## POST /experiments/:id/configs
Body: `{ "config_id": "b@v2" }`. Runs that config on the experiment's task snapshot with the same N, and keeps
the existing trials. Returns `{ "id": "fc34649f", "added": 38 }`. Returns `400` if the config is already in the experiment.

## POST /experiments/:id/rerun
Creates a **new** experiment with the same config versions, task snapshot and N. Returns `{ "id": "ac719ef6", "trials": 12 }`.

## GET /trials/:id
The full record: everything from `/trials` plus `grader`, `trace`, `task` (from the snapshot) and `config` (spec).
```json
{
  "id": "fc34649f-0", "status": "done", "passed": 0, "failure_category": "wrong_answer",
  "grader": { "type": "exact", "reasoning": "expected '215', got '214' (json answer field)" },
  "trace": {
    "events": [
      { "type": "session_start", "ts": 1795260735.32, "index": 0 },
      { "type": "user", "ts": 1795260735.32, "content": "A train leaves at 09:40 ..." },
      { "type": "assistant", "ts": 1795260735.75, "content": "{\"answer\": \"214\"}", "tool_calls": [] },
      { "type": "session_end", "ts": 1795260735.75, "index": 0, "stop_reason": "completed" }
    ],
    "final_answer": "{\"answer\": \"214\"}", "stop_reason": "completed",
    "input_tokens": 100, "output_tokens": 10, "available_tools": ["read_file", "write_file", "list_files"]
  },
  "task": { "id": "arith-01", "prompt": "...", "grader": { "type": "exact", "expected": "215" } },
  "config": { "harness": "baseline", "model": "gpt-4o-mini", "...": "..." }
}
```
Trace event types: `session_start`, `user`, `assistant` (`content`, `tool_calls`), `tool_call` (`name`,
`arguments`), `tool_result` (`name`, `output`, `error`), `memory_read`, `memory_write` (`content`),
`skill_used` (`name`), `session_end` (`stop_reason`). The trial's `error` field holds the error message when
`status` is `error`. Timeouts store a message there too.

## Server settings (environment variables)
| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `3000` | HTTP port |
| `MAX_CONCURRENCY` | `4` | trials running at once; use 1–2 for free, rate-limited models |
| `MAX_ATTEMPTS` | `4` | attempts per trial on retryable errors (rate limits, 5xx) |
| `BACKOFF_MS` | `2000` | first retry delay; doubles each attempt, plus jitter |
| `BREAKER_ERRORS` | `3` | consecutive initial errors that stop a config |
| `DB_PATH` | `../data/evals.db` | SQLite file |
| `EVALS_ROOT` | `..` | folder with `tasks/`, `skills/`, `models.yaml` |
| `WORKER_CMD` | `python3 ../worker/run_trial.py` | how to start one trial |
