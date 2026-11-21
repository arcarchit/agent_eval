# Agent Evals Platform

Evaluate any **agent harness** (Hermes Agent, a baseline tool loop, your own) running with any **model**, and
answer *"did this change make the agent better, worse, or the same, and how sure are we?"* with evidence.

- **Compare configs** (harness × model × skills, memory, tools, temperature, budgets) over N repeated trials.
- **Grade** with exact match, regex, file-state checks, hidden unit tests, or a validated LLM judge.
- **Trust the verdict**: paired bootstrap confidence intervals, an explicit "this may be noise", per-task deltas,
  and cost / latency / tokens next to quality.
- **See why**: the full trajectory of every trial (messages, tool calls, memory, skills, grader reasoning).
- **Extend**: a new model is a YAML entry; a new harness is one adapter class.

## Quick start

```bash
cp .env.example .env              # add OPENROUTER_API_KEY
docker compose up --build         # http://localhost:3000
# or, without Docker (macOS: brew install node@22 uv):
./scripts/run-local.sh
```
Try one prompt first: `./scripts/run-local.sh cli --model gpt-4o-mini "What is 17% of 2340?"`.
Then follow [GUIDE §2](docs/GUIDE.md#2-your-first-comparison) for your first comparison.

## Documentation

| Doc | For | Contents |
|-----|-----|----------|
| [docs/SUBMISSION.md](docs/SUBMISSION.md) | **assignment reviewers** | the deliverables in the assignment's order, with status and links; video script |
| [docs/GUIDE.md](docs/GUIDE.md) | users | quick start, first comparison, **how to add a task, model, harness or skill**, judge validation, troubleshooting |
| [docs/API.md](docs/API.md) | integrators | every REST endpoint with real request/response examples, statuses, server settings |
| [docs/FEATURES.md](docs/FEATURES.md) | buyers, users | feature list, **known limitations**, roadmap |
| [docs/USAGE_AND_COST.md](docs/USAGE_AND_COST.md) | anyone running experiments | LLM calls, tokens, turns and cost per experiment; rate limits |
| [docs/DESIGN.md](docs/DESIGN.md) | engineers | architecture, adapter interfaces, data model, isolation, statistics |

The experiment write-up is in [docs/EXPERIMENT.md](docs/EXPERIMENT.md) and the judge-validation story in
[docs/JUDGE_VALIDATION.md](docs/JUDGE_VALIDATION.md). Internal build notes (plan, decisions and trade-offs, learning
notes) are in `docs/internal/`.

## Project layout

```
api/          NestJS REST API, SQLite, trial runner (pool, retries, timeouts, resume, cancel), statistics
ui/           Vue 3 UI: configs, experiments, comparison, trial trajectories
worker/       Python: run_trial.py, harness adapters (hermes, baseline), graders, failure categories, cli.py,
              power_analysis.py (how N was chosen)
tasks/        26 sample tasks (YAML), including 5 hard ones
skills/       skills that configs can enable
models.yaml   model registry and prices
scripts/      run-local.sh (run without Docker)
judge_validation/   hand-labelled answers for checking the LLM judge
```

## Tests

```bash
python -m pytest worker/tests        # 20 tests: graders, harnesses, isolation, provider errors, failure rules, judge
cd api && npm test                   # 11 tests: statistics, config versioning, runner retry/timeout/crash/resume/cancel/breaker
```
The tests use a fake model and a fake worker, so they need no API key.

## Assumptions

- Models are reached through OpenAI-compatible APIs (OpenRouter by default). Prices in `models.yaml` are entered by
  hand; check them before quoting costs.
- A timeout counts as a **failure**. Provider errors (out of credits, bad model id, rate limits after retries) and
  crashes count as **errors**: excluded from statistics and shown separately.
- "Memory" means memory across sessions *within one trial*. Trials never share state.
- The judge's reference labels were written by the author, not by independent annotators.
- The sandbox (temp dir + process + timeout) suits trusted agents and tasks; see [FEATURES.md](docs/FEATURES.md#known-limitations).
