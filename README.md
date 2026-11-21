# Agent Evals Platform

Evaluate any **harness** (Hermes Agent, a baseline tool loop, …) running with any **model**, and answer
*"did the agent get better, worse, or stay the same, and how sure are we?"* with evidence.

- `PLAN.md`: the build plan and what to say about each step
- `docs/DESIGN.md`: architecture, interfaces, data model, statistics, known limits, cost
- `docs/EXPERIMENT.md`: the real experiment (Hermes with skills on vs off)
- `docs/JUDGE_VALIDATION.md`: a real judge instability found in the first run, and the fix (before/after)
- `docs/LEARNING_NOTES.md`: every question asked while building this, with answers (stack, request flow, isolation, models)
- `docs/DECISIONS.md`: options considered, trade-offs and criteria for each design decision

## Run it

```bash
cp .env.example .env          # put your OPENROUTER_API_KEY in it
docker compose up --build     # open http://localhost:3000
```

Then:
1. **Configs**: create two configs, e.g. `hermes-skills` (harness hermes, skills `acme-billing` + `acme-style`) and
   `hermes-noskills` (same, no skills).
2. **Experiments**: tick both configs, set N (trials per task), click Run.
3. Open the experiment: the pass rate with its 95% CI, the A-vs-B verdict, per-task deltas, cost and latency.
4. Click a cell in the per-task table, then a trial, to see its full trajectory and why it passed or failed.
5. Export the results as JSON or CSV with the links at the top of the experiment page.
6. **Stop** a running experiment (or one config) with the buttons on the experiment page. A config whose first
   3 trials all error, for example because of a wrong model id, is stopped automatically.
7. **Reuse results**: if one config failed, fix it (a new version), then use "Add a config to this experiment".
   It runs on the same tasks and N, and the finished trials of the other config are kept.

**What needs a restart?** `tasks/`, `skills/` and `models.yaml` are mounted into the container and read fresh each
time, so edits apply to the next config or experiment without a restart. `.env` (API keys, `MAX_CONCURRENCY`) needs
`docker compose up -d`; note that `docker compose restart` does not re-read `.env`. Code changes (`api/`, `ui/`,
`worker/`) and `Dockerfile` changes need `docker compose up --build`. A restart is safe: interrupted trials resume.

## Run without Docker

```bash
brew install node@22 uv            # macOS; on Linux install Node >= 22.13 and uv
cp .env.example .env               # add OPENROUTER_API_KEY
./scripts/run-local.sh             # first run installs Hermes into ~/hermes-agent (a few minutes), then http://localhost:3000
./scripts/run-local.sh cli "What is 17% of 2340?"          # one prompt through Hermes, no server needed
```
The script installs Hermes (same pinned commit as Docker) into its own virtualenv, loads `.env`, builds the UI and
the API, and starts the API, which serves the UI. Set `HERMES_DIR` to install Hermes elsewhere. The repo path must not
contain spaces. For hot-reloading UI work, also run `cd ui && npm run dev` (http://localhost:5173).

## Try one prompt from the terminal

```bash
# without Docker
./scripts/run-local.sh cli --harness hermes "What is 17% of 2340?"
# with Docker: `exec` needs the container running (docker compose up -d); `run --rm` starts a one-off container instead
docker compose run --rm evals /opt/hermes/.venv/bin/python /app/worker/cli.py --harness hermes "What is 17% of 2340?"
docker compose exec evals /opt/hermes/.venv/bin/python /app/worker/cli.py --harness hermes --skills acme-billing --task skill-01 --keep
```
It prints every trace event, the final answer, tokens and cost (and the grade when you pass `--task`).
`--keep` leaves the trial folder on disk and prints its path. Inside it, `home/hermes/` is Hermes' private
home for that trial (`memories/MEMORY.md`, `skills/`, `logs/agent.log`) and `workspace/` holds the files the agent wrote.

## Tests

```bash
python -m pytest worker/tests        # graders, baseline harness, memory isolation, failure rules, judge parsing
cd api && npm test                   # statistics, versioned configs, runner: retry / timeout / crash / resume
```
The tests use a fake model and a fake worker, so they need no API key.

## Validate the LLM judge

```bash
python worker/judge_validation.py    # needs OPENROUTER_API_KEY; writes judge_validation/report.json
```
It runs the judge on 18 hand-labelled answers (`judge_validation/labels.json`) and reports accuracy,
Cohen's kappa, and every disagreement.

## How to add a task

Add `tasks/<id>.yaml`:

```yaml
id: my-task
category: files
needs: skill:acme-billing        # optional, documents what should make configs differ
memory_setup: ["Remember X=1"]   # optional: earlier sessions, run before the prompt in a NEW session
setup_files: { data.csv: "a,b\n1,2\n" }   # optional: files placed in the workspace
prompt: "..."
grader: { type: exact, expected: "42" }
```

Grader types:

| Type | Fields | Use for |
|------|--------|---------|
| `exact` | `expected` | short answers; compared after trimming and lower-casing |
| `regex` | `pattern` | answers with several valid formats |
| `file_check` | `path` + one of `equals`, `number`, `json_equals`, `contains` | state of the workspace after the run |
| `unit_test` | `test_code` | code the agent wrote; the test file is added only after the agent finishes |
| `llm_judge` | `rubric` | open-ended writing; validate it with `judge_validation.py` |

No restart is needed: the API reads tasks when an experiment starts, and stores a snapshot of them in the experiment.

## How to add a model

Add an entry to `models.yaml`. Any OpenAI-compatible endpoint works: OpenRouter, xAI (Grok), Groq, a local vLLM or Ollama, LiteLLM.
`models.yaml` already has commented-out examples for Grok, Groq and Ollama, and three free OpenRouter models (verify their IDs).

```yaml
  my-model:
    provider_model: mistralai/mistral-small
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    price_per_mtok: { input: 0.1, output: 0.3 }
```

## How to add a harness

1. Create `worker/harnesses/my_harness.py` with a class that implements
   `Harness.run(task, config, model, workspace, home) -> Trace` (see `worker/harnesses/base.py`).
   Record what happens with `trace.add("tool_call", ...)` and the other event types, then fill in
   `final_answer`, the token counts and `stop_reason`.
2. Register it in `worker/harnesses/__init__.py` and add its name to `HARNESSES` in `api/src/catalog.ts`.

The platform core (runner, graders, statistics, UI) does not change.

## Project layout

```
api/            NestJS: REST API, SQLite, trial runner (pool, retries, timeouts), statistics
ui/             Vue 3: Configs, Experiments, Experiment (compare), Trial (trajectory)
worker/         Python: run_trial.py, harness adapters, graders, failure categories, judge validation
tasks/          21 task YAML files
skills/         skills that configs can enable (acme-billing, acme-style)
models.yaml     model registry and prices
judge_validation/labels.json   hand-labelled answers for checking the judge
```

## Assumptions

- All models are reached through OpenRouter's OpenAI-compatible API, so one key covers both providers.
- Prices in `models.yaml` were copied by hand; check them before quoting costs.
- A trial that times out counts as a **failure** (the agent was too slow). A trial that keeps hitting rate limits
  after 4 attempts, or crashes, is an **error**: it is excluded from statistics and shown separately.
- "Memory" means memory across sessions *within one trial* (the `memory_setup` sessions, then the prompt).
  Trials never share memory, so trials stay independent.
- The judge labels in `judge_validation/labels.json` were written by the author, not by independent annotators.
- The sandbox is a temp directory plus a separate process plus a timeout, not a container per trial. This is
  fine for this trusted task set, and not fine for untrusted code (see `docs/DESIGN.md`, Known limits).
