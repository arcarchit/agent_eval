# Plan: Agent Evals Platform (HighLevel take-home)

The goal is to keep the code small enough that you can explain every file in an interview.

## 1. What we build (the whole system in one picture)

```
 Vue UI  ──HTTP──▶  NestJS API  ──spawn one process per trial──▶  Python worker
 (ui/)              (api/)                                         (worker/)
                    - stores configs, experiments, trials           - runs ONE task with ONE config
                      in SQLite                                     - harness adapter: hermes | baseline
                    - queue + retries + timeouts                    - grades the result
                    - statistics (bootstrap CI)                     - prints JSON (trace + score) to stdout
```

- **Why one process per trial?** Each trial gets its own process, its own temp folder and its own
  Hermes home directory. Nothing leaks between trials, and a crash or a hang only affects that trial,
  which we kill on timeout.
- **Why Python for the worker?** Hermes Agent is a Python library.
- **Why SQLite?** It is one file, needs no server, and is enough for a local tool.

## 2. Steps (in the order to build and explain them)

| # | Step | Files | What to say in the interview |
|---|------|-------|------------------------------|
| 1 | Task set: 20 YAML files | `tasks/*.yaml`, `skills/` | Each task has a prompt and a grader. Some tasks need a skill or memory, so configs can differ. |
| 2 | Graders | `worker/graders.py` | Deterministic (exact, regex, file check, unit tests) and an LLM judge with a rubric. |
| 3 | Judge validation | `worker/judge_validation.py`, `judge_validation/labels.json` | Run the judge on hand-labelled examples and report agreement and Cohen's kappa. |
| 4 | Model registry | `models.yaml` | Every model is reached through one OpenAI-compatible API (OpenRouter). A new model is a YAML entry. |
| 5 | Harness adapters | `worker/harnesses/` | `Harness.run(task, config, workspace) -> Trace`. A new harness is one new file plus one line in the registry. |
| 6 | Trial runner | `worker/run_trial.py` | Reads a job from stdin, runs the harness, grades, classifies the failure, writes JSON to stdout. |
| 7 | API + DB | `api/src/*` | Immutable versioned configs, experiments, trials. The runner has a concurrency pool, timeout, retry with backoff, and resume after restart. |
| 8 | Statistics | `api/src/stats.ts` | Paired bootstrap over tasks gives a 95% CI for "A − B". If the CI contains 0, the result is noise. Per-task deltas are reported as well. |
| 9 | UI | `ui/src/*` | Four pages: Configs, Experiments, Compare, Trial (trajectory viewer). |
| 10 | Docker + docs | `docker-compose.yml`, `README.md`, `docs/DESIGN.md` | One command to start. Design doc with limits and cost formula. |
| 11 | Real experiment (you run it) | `docs/EXPERIMENT.md` | Hermes with skills on vs off, same model, N=5. Needs your OpenRouter key. |
| 12 | Video (you record it) | | Create a config, run, compare, open a failed trial. |

## 3. Scope cuts (said out loud = "judgment")
- No Redis or job queue: an in-process pool with a concurrency limit. Unfinished trials resume when the API restarts.
- No auth, no multi-user.
- Sandbox = temp directory + separate process + timeout, not a container per trial (documented as a limit).
- Failure categories are simple rules over the trace, not ML.

## 4. Interview cheat sheet

**Reading order (about 1,100 lines of backend code; the UI adds about 500):** `worker/harnesses/base.py` → `worker/run_trial.py` →
`worker/harnesses/baseline.py` → `worker/harnesses/hermes.py` → `worker/graders.py` →
`api/src/runner.service.ts` → `api/src/stats.ts` → `api/src/api.controller.ts`.

**60-second pitch:** "A trial is one task under one config. The NestJS API stores configs, experiments and
trials in SQLite. It runs each trial as a separate Python process, which gives me isolation and a hard timeout.
The worker calls a harness adapter (Hermes or my baseline loop), which returns a trace. A grader scores it and
a rule labels the failure. For comparison, I average the N trials per task, take paired per-task differences,
and bootstrap over tasks for a 95% CI. If the CI contains zero, the UI says it may be noise."

**Likely questions:**
- *Why bootstrap over tasks and not trials?* Trials of one task are correlated. Treating them as independent
  makes the CI too narrow.
- *Why not McNemar?* It needs one paired binary outcome. I have N trials per task with no natural pairing,
  so I pair by task instead.
- *How is a rate limit different from a failure?* The worker exits with code 2, and the runner retries with
  backoff. After 4 tries the trial is an "error": excluded from stats and shown as a count. A timeout is a real
  failure (the agent was too slow).
- *How do you know the judge is right?* 18 hand-labelled answers, including subtle ones. I report accuracy
  and Cohen's kappa, and I trust it only if kappa ≥ 0.8.
- *How do you add a harness?* One class with `run(task, config, model, workspace, home) -> Trace`, plus one line
  in the registry.
- *How is a config versioned?* It is immutable. The same name with new settings creates v2. The experiment also
  stores a snapshot of the tasks.
- *How does memory work without leaking?* Memory lives in the trial's temp home dir. Inside a trial, the
  `memory_setup` messages run as earlier sessions, then the prompt runs in a fresh session. Only memory can carry
  the fact across. Trials never share a home dir.
- *Biggest weakness?* 21 tasks can only detect large effects (about 13 points or more). The temp-dir sandbox is
  not a security boundary. The judge labels are mine, not independent.
