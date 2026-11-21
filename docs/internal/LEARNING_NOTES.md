# Learning notes: questions I asked while building this, and the answers

Re-read this before the interview. Questions are grouped by topic, not by the order I asked them.
Line numbers refer to the code at the time of writing; if they drift, search for the function name.

---

## 1. The stack, for someone who last did Java web apps

**Q: What are NestJS, Vue 3 and TypeScript?**

| Now | Java-era equivalent |
|-----|---------------------|
| **TypeScript** | JavaScript plus types, checked at compile time like Java. `tsc` compiles it to plain JS. |
| **Node.js** | The server runtime, in the role of Tomcat/JVM. |
| **NestJS** | Spring MVC for Node. `@Controller('api')` + `@Get('experiments/:id')` = "every path has a controller method". Services are injected through constructors, like Spring `@Autowired`. |
| **Vue 3** | Replaces JSP. It runs in the browser: a `.vue` file holds template + script + CSS, calls the API with `fetch`, and renders the JSON. |
| **vue-router** | Maps a browser URL like `#/trials/abc` to a page component. |
| **Vite** | Build tool that bundles the `.vue` files into static JS/CSS (`ui/dist`), which NestJS serves. |

The flow is the familiar one: browser → `GET /api/...` → controller method → SQL on SQLite → JSON back.

**Q: Can I browse the SQLite database like MySQL Workbench?**
Yes:
- **DB Browser for SQLite** (`brew install --cask db-browser-for-sqlite`), TablePlus, or DBeaver: open `data/evals.db`.
- Command line: `sqlite3 data/evals.db`, then `.tables`, then `SELECT config_id, task_id, passed FROM trials LIMIT 10;`

There are 3 tables: `configs`, `experiments` and `trials`. The `trials.trace` column holds JSON.
Don't write to the database from the app while experiments are running.

---

## 2. Following one request through the code

**Q: Trace one request end to end (you click Run).**
1. **Browser**: `ui/src/pages/Experiments.vue` `start()` → `POST /api/experiments`.
2. **Controller**: `api/src/api.controller.ts` `createExperiment` checks the configs and loads the task YAML files. `startExperiment`
   then saves the experiment with a **copy of the tasks**, inserts one `pending` trial row per task × config × repeat,
   calls `runner.enqueue(ids)`, and returns immediately.
3. **Runner**: `api/src/runner.service.ts` `pump()` keeps at most `MAX_CONCURRENCY` (4) trials running. `runTrial()` loads the
   trial, task and config, then `runWorker()` spawns `python worker/run_trial.py`, writes the job JSON to its stdin,
   and kills it with SIGKILL at the timeout.
4. **Worker**: `worker/run_trial.py` `run()` (line 34) creates a fresh temp folder with `workspace/` and `home/`. It calls the
   harness with `agent_view(task)` (line 45: the task **without** the grader), then grades with the full task (line 47),
   classifies the failure, and prints one JSON line.
5. **Harness**: `worker/harnesses/hermes.py`:
   - copies the enabled skills into the private Hermes home (line 24);
   - creates `AIAgent` (line 45) and calls `run_conversation` (line 52). Model and tool calls happen inside Hermes.
   - a failed model call raises an error instead of being graded (line 57);
   - converts Hermes' messages into trace events (line 62).
6. **Grader**: `worker/graders.py` `grade()` pulls `{"answer": ...}` out of the reply, compares it, runs the hidden unit
   tests, or calls the judge (5 votes).
7. **Back in the runner**: exit code 0 → save the result with one UPDATE; exit 2 → retry with backoff; exit 1 → `error`.
8. **Browser**: the experiment page asks for `GET /api/experiments/:id` every 3 s. `GET .../compare` runs `compare()` in `api/src/stats.ts`.

**Q: Where is the system prompt? Where are skills and tools loaded?**
- **Baseline** builds everything itself, in `worker/harnesses/baseline.py`:
  - system prompt at line 92, with skill names and the memory note appended after it;
  - tools chosen at lines 33 (file), 37 (terminal), 39 (memory) and 42 (skills);
  - a skill's text is loaded when the model calls `read_skill` (line 77).
- **Hermes** builds its own ~6,500-character system prompt (identity, skills index, memory, tool rules). We control
  what goes into it: which skills are copied in (line 24), and which toolsets are enabled (from line 32, passed at line 49).
  Interview line: *"Hermes owns its prompt; my adapter controls what it can see and use."*

**Q: Why does my editor say `Import "run_agent" could not be resolved` in hermes.py?**
Hermes is installed in its **own** Python virtualenv (`/opt/hermes/.venv` in Docker, with `/opt/hermes` on
`PYTHONPATH`). Your editor is using a different Python interpreter that doesn't have Hermes, so it can't find the module.
It works at runtime. The line now carries `# pyright: ignore[reportMissingImports]` and a comment saying why.
To get rid of it for real, point VS Code's interpreter at Hermes' venv, or add the Hermes folder to `python.analysis.extraPaths`.
The import sits *inside* `run()` on purpose: `HERMES_HOME` must be set before Hermes is imported, and keeping it
local means the baseline harness and the tests don't need Hermes installed.

---

## 3. Config settings

**Q: What do the `file`, `terminal` and `memory` checkboxes do?**
- **file**: the agent can read and write files in its temp workspace. The `file-*`, `code-*` and `skill-04` tasks need it.
- **terminal**: the agent can run shell commands. It helps the coding tasks, but it is the riskiest tool (see section 4).
- **memory**: the agent gets a save-a-note tool that carries across the sessions **inside one trial**. `mem-01` and
  `mem-02` need it; with memory off they fail by design.

**Q: Recommended values?**

| Setting | Value | Why |
|---------|-------|-----|
| Temperature | 0 for A-vs-B comparisons | Less noise. Results still vary, so you still need N > 1. |
| Max steps | 10 (15 if you see `step_exhaustion`) | Enough for read → write → check → answer. |
| Max tokens per reply | 1024 | The answers are short. |
| Timeout | 300 s for Hermes, 120 s for baseline | Timeouts count as failures, so don't set them too tight. |

For the experiment, change one axis only (skills) and keep everything else identical.

---

## 4. Isolation and safety

**Q: Where is memory stored? In SQLite?**
No. During a trial, memory is a file in that trial's temp folder: `home/hermes/memories/MEMORY.md` for Hermes,
`home/memory.json` for the baseline. The folder is deleted when the trial ends. SQLite stores only the **record**
of memory use: the `memory_write` and `memory_read` events inside `trials.trace`.

**Q: We delete the folder when a trial ends. What happens when trials run in parallel?**
Each trial is its own **process** with its **own** temp folder: `tempfile.TemporaryDirectory(prefix="trial-")`
(`run_trial.py` line 36) creates a unique random name such as `/tmp/trial-k3j9x2/`. Four trials running at
once have four different folders, and each one deletes only its own. Hermes' `HERMES_HOME` setting is global to a
process, which is exactly why there is one process per trial: in a shared process, parallel trials would overwrite each other's setting.
Caveat: all trial folders sit under the same `/tmp` and run as the same OS user, so an agent with the terminal tool
*could* `ls /tmp` and read another running trial's folder. Folder separation stops accidental leaks, not a
deliberately snooping agent.

**Q: If I run in Docker, is the terminal tool fine?**
Your Mac is protected, but the eval is not cheat-proof. Inside the container the agent can reach:
- `/app/tasks`, which holds the expected answers;
- the SQLite database;
- environment variables, including the API key, which the agent's own process needs to call the model.

Keep `terminal` off for experiments.

**Q: How do we make sure the agent doesn't read the expected answers? Do we pass it the answer, or just the prompt?**
- **The model only ever sees** the prompt, the `memory_setup` messages and the task's setup files. The harness used to
  receive the whole task dict, including the grader (it never put the grader into the prompt). Now `agent_view()` in
  `run_trial.py` strips the grader before the harness gets the task, so no adapter can leak it, and a test checks this
  (`test_harness_never_sees_the_grader`).
- **Hidden unit tests** are written into the workspace only **after** the agent finishes.
- **Still possible**: with the terminal tool (and maybe Hermes' file tool on absolute paths) the agent could open
  `/app/tasks/*.yaml`. Real fixes, in order of effort:
  1. Run the worker as a separate OS user that cannot read `/app/tasks` or `/app/data`.
  2. Run each trial in its own container that gets only the workspace, with no task files and no database.
  3. Keep the API key out of the agent's process by putting a small model proxy in front of it.

  All three are documented as limits, not built. For the interview: *"the grader is never in the agent's context;
  filesystem-level isolation is the next step and I know exactly how to do it."*

**Q: Hermes is known for editing skills. Is that enabled, and is it isolated from the baseline?**
Yes, it's enabled: turning skills on also gives Hermes `skill_manage`, which lets it create and edit skills. The edits go to the trial's **private copy**
(line 24 copies the skills into the temp Hermes home), which is thrown away afterwards. The repo's `skills/` folder, other
trials and the baseline never see the change.
One leak inside a trial was observed: with memory **off**, Hermes saved a fact from `mem-01` *as a skill* and
answered from it. That is why the skills experiment excludes `mem-*` tasks.

**Q: Where can I see Hermes' private folder?**
It's deleted after every trial. To inspect one, run the CLI with `--keep` (section 5). It prints the folder path. Inside:
- `home/hermes/memories/MEMORY.md`
- `home/hermes/skills/`
- `home/hermes/logs/agent.log`
- `workspace/` (the files the agent wrote)

---

## 5. Running and debugging

**Q: Can I send a prompt from the command line and see the response?**
Yes, with `worker/cli.py`:
```bash
docker compose run --rm evals /opt/hermes/.venv/bin/python /app/worker/cli.py --harness hermes "What is 17% of 2340?"
docker compose exec evals /opt/hermes/.venv/bin/python /app/worker/cli.py --harness hermes --skills acme-billing --task skill-01 --keep
```
It prints every trace event, the final answer, tokens and cost, plus the grade when you use `--task`.

**Q: I couldn't click a cell to see trajectories.**
The click worked, but the trial list appeared at the bottom of the page, below all 21 task rows. The page now
scrolls to the list and highlights the cell you clicked. Click a trial number to open its trajectory.

**Q: I only see one file on GitHub. Did we commit?**
Yes. GitHub opens on `main`, which only has the original README. The work is on the branch
`claude/interview-assignment-prep-ath0vy`: pick it in the branch dropdown, or merge it into `main` before submitting.

---

## 6. Dependencies

**Q: `npm install` reported 10 vulnerabilities (2 critical). Should I worry?**
Low real risk: they were denial-of-service bugs in Express internals (`body-parser`, `multer`, `qs`) and, for the
critical ones, in the test runner (`tinypool`), which never runs in production. The app only runs on localhost. They were
fixed anyway by upgrading NestJS 10 → 11, vitest → 4 and vite → 7, so `npm audit` now shows 0.
The `install-scripts` warning is a newer npm safety feature asking you to approve packages that run setup scripts
(`esbuild`, `fsevents`, `@nestjs/core`). These are normal; you can ignore it.

---

## 7. Models and cost

**Q: We ran out of credits. Are there cheaper or free alternatives?**
- The whole first run cost about **$0.08**. The problem was an empty balance, not the price. Adding $5 of credit is the simplest fix.
- **OpenRouter free models** (ID ends in `:free`): $0, but rate-limited per minute and per day. Rate-limited trials are
  retried and then end as `error`. They must support **tool calling**, or Hermes can't use its tools.
  Added to `models.yaml`: `particle-1-free`, `nemotron-3-ultra-free`, `gemma-4-26b-a4b-free`. These IDs could not be
  verified from the build environment, so check each one on openrouter.ai/models.
- **Local and free**: Ollama with `qwen3:8b` (commented example in `models.yaml`). Expect weaker tool use and slower runs.

**Q: Can I use Grok instead of OpenRouter? How much has to change?**
No code changes, only configuration. Every model is just a `base_url` + model name + key name in `models.yaml`, and
**xAI (Grok)** serves the same OpenAI-compatible API at `https://api.x.ai/v1`. Uncomment the `grok` entry, set
`XAI_API_KEY` in `.env`, and restart. If you meant **Groq** (fast hosting of open models, with a free tier), it works the
same way through `https://api.groq.com/openai/v1` (entry included). To change the **judge** model too, set
`judge_model:` to that entry. Check each provider's current model names and prices. One caveat: Hermes has its own
provider detection based on the URL. It should accept any OpenAI-compatible URL, but run one `cli.py` prompt first.
*This is the "a new model is a config change, not a code change" design point; use it in the interview.*

---

## 8. What the first real experiment taught us (run e181b969)

- **OpenRouter credits ran out mid-run.** 116 of 210 trials got HTTP 402 and were **graded as `wrong_answer`**. That was a
  platform bug. Now fixed: provider errors end as `error`, are excluded from the statistics, and are shown as a count.
- **Exact match was too strict.** "The trip is 215 minutes." failed against expected `215`. The answer tasks now ask for
  `{"answer": ...}` and the grader extracts that field.
- **The judge was unstable at temperature 0.** The same answer got 3 passes and 4 fails. Full story in `docs/JUDGE_VALIDATION.md`.
- **The memory tasks made no sense in a skills experiment** (memory was off in both configs), so they are now excluded.
- Good trajectories to show: `e181b969-39` vs `-38` (skill-05), and `-115` vs `-30` (skill-01).

---

## 9. Operating experiments

**Q: I started an experiment with a wrong OpenRouter model id. How do I stop it?**
- **Stop experiment** at the top of the experiment page, or **Stop this config** in that config's row.
  Pending trials become `cancelled` and never run; running ones finish; finished trials are kept.
  (API: `POST /api/experiments/:id/cancel`, with an optional `{"config_id": "..."}`.)
- **Automatic**: a wrong model id makes the provider reject every call. Those trials end as `error`, never graded, and cost $0. After a config's
  first 3 finished trials are all errors, a **circuit breaker** stops the rest of that config by itself
  (`maybeTripBreaker` in `api/src/runner.service.ts`, threshold `BREAKER_ERRORS`).
- In the interview: *"failing fast on a misconfigured config protects both budget and time; the breaker only trips
  when nothing has succeeded, so a few transient errors in a healthy config don't stop it."*

**Q: If I change `models.yaml`, do I need to restart Docker? When do I need to restart?**

| Change | What to do | Why |
|--------|------------|-----|
| `models.yaml`, `tasks/*.yaml`, `skills/` | Nothing. Just refresh the UI. | These are mounted into the container (`docker-compose.yml`) and read fresh on every request and every trial. |
| `.env` (API keys, `MAX_CONCURRENCY`) | `docker compose up -d` | Environment variables are set when the container is created. `docker compose restart` does **not** re-read `.env`. |
| Code in `api/`, `ui/`, `worker/`, or the `Dockerfile` | `docker compose up --build` | The code is copied into the image at build time. |

A restart is safe at any time: trials that were running go back to `pending` and run again.
**Important:** editing a model in `models.yaml` does **not** change existing configs. Each config stores a snapshot
of its model entry when it is saved (`model_spec`). To use the corrected model id, save the config again: same name,
same settings. That creates a **new version** (`name@v2`), because the snapshot changed. This keeps every past result
pointing at exactly the model that produced it.

**Q: Of two configs, one worked and one didn't. Can I reuse the working config's data?**
Yes:
1. Fix the model id in `models.yaml`.
2. Save the broken config again to get `name@v2`.
3. Open the old experiment and use **Add a config to this experiment**.

The new config runs on the **same task snapshot and N**. The working config's finished trials are kept, and the
comparison table now pairs them by task (`POST /api/experiments/:id/configs`). The broken `v1` stays in the
table with its errors, and you just pick A = working config and B = `v2`.
Caveat to mention: the two configs ran at different times, so if the provider changed the model in between, that is
not controlled for. For a final, clean result, run both configs together in one fresh experiment. Use reuse to save money while iterating.

---

## 10. Running it

**Q: `docker compose exec ...` says `service "evals" is not running`.**
`exec` runs a command *inside a container that is already running*. The container wasn't up: either it was never
started, it was stopped, or it crashed on start.
- Check it: `docker compose ps -a`. If the status is `Exited`, see why with `docker compose logs evals`.
- Start it in the background, then use `exec`: `docker compose up -d`.
- Or don't depend on it: `docker compose run --rm evals <command>` starts a throwaway container just for that
  command and removes it afterwards. Same image, same `.env`, same mounted files.

**Q: How do I run it without Docker?**
`./scripts/run-local.sh` (needs `brew install node@22 uv`). It:
1. installs Hermes at the pinned commit into `~/hermes-agent/.venv`, the first time only (uv downloads Python 3.14 itself);
2. loads `.env`;
3. sets `PYTHONPATH` (so Python finds Hermes) and `WORKER_CMD` (which Python runs the trials);
4. builds the UI and the API, then starts on http://localhost:3000.

`./scripts/run-local.sh cli "prompt"` runs one prompt with no server. These are the same environment variables the
Dockerfile sets; the script just sets them on your Mac instead of inside the container.

---

## 11. Rate limits, usage, and the docs

**Q: `HTTP 429: Provider returned error` with a free model. Is it a time limit, or did I hit 1,000 requests? The portal shows 971.**
429 = too many requests. With OpenRouter there are two kinds:
- **Your own quota** for `:free` models: about 20 per minute, plus a daily cap that is higher once you have bought
  credits. The error then names the limit ("free-models-per-min" or "free-models-per-day").
- **"Provider returned error"**: the *upstream* provider hosting that free model is overloaded or rate-limiting
  everyone using it through OpenRouter. That wording points here, not to your daily cap. With 971 used, you were
  also close to a 1,000 cap, so either could happen next.

To see which one it was, open the request dump that Hermes printed and look for the provider's message
(`python3 -m json.tool <dump.json> | grep -i -B2 -A5 "error\|limit"`).
Watch out for **retry amplification**: Hermes retries each call 3 times, then the platform retries the whole
trial up to 4 times, so one rate limit can burn ~12 requests. On free models set `MAX_CONCURRENCY=1` and consider
`MAX_ATTEMPTS=2`. The CLI now prints a short `MODEL ERROR` message instead of a Python traceback.

**Q: How many LLM calls, tokens and turns does an experiment use? Is the grader run more than once?**
Everything is in `docs/USAGE_AND_COST.md`. The short version, measured with Hermes + gpt-4o-mini:
- ~2.5–3 model calls per trial, ~1.7–1.9k input tokens per call, ~120 output tokens per trial.
- 1 user turn per trial (2 for memory tasks).
- The grader runs **once** per trial. Deterministic graders make **0** LLM calls; `llm_judge` makes **5** calls
  (5 votes on the same response, majority wins).
- Skills experiment (190 trials): ~510 agent calls + 150 judge calls ≈ 660 requests, ≈ $0.17 on gpt-4o-mini.
- Judge validation: 19 examples × 5 votes = 95 single-turn calls, ≈ $0.01.

**Q: How should docs be organised if we sell this as a product?**
By reader:
- `README` is a one-page entry point;
- `GUIDE` holds the tutorials (add a task, model, harness, skill);
- `API` is the endpoint reference, with real responses;
- `FEATURES` lists features, known limitations and the roadmap; being honest about limits builds trust;
- `USAGE_AND_COST` covers calls, tokens, cost and rate limits;
- `DESIGN` is for engineers.

Interview and build notes go in `docs/internal/`, so a customer never sees "interview cheat sheet".

---

## 12. Metrics, leaks, hard tasks, and the statistics question

**Q: Can the trajectory show how long each step took?**
Yes. Every event has a timestamp, and the trial page shows on the right how long each step took (the time since
the previous event):
- "model call 1.4s" on an ASSISTANT event is the LLM latency;
- "tool 0.3s" on a TOOL RESULT is the tool's run time;
- "harness setup" on the first USER event is Hermes building its agent (~3–4 s).

Hermes' adapter used to stamp every event with the moment it converted the messages, so every step showed 0 s. It
now keeps Hermes' own per-message timestamps.

**Q: Can the per-config table and the per-task table show turns, latency and cost?**
Yes:
- per config: **LLM calls / trial** (model turns) and **user turns / trial** (sessions);
- per task cell: average latency, LLM calls, user turns and cost per trial.

Both counts are stored per trial (`llm_calls`, `turns` columns); older databases are migrated and backfilled from
the stored traces on startup.

**Q: The code-01 trajectory seemed to use the same unit tests as the grader. Are we leaking them?**
No. Checked three ways:
1. The harness receives the task **without** the grader (`agent_view()`; a test asserts it).
2. The test file is written into the workspace only **after** the harness returns; a new test asserts the workspace
   holds only the setup files while the agent runs.
3. In the first real run, none of the 6 code-01 trajectories contained our test strings or the test file.

What you most likely saw: the model writing its own checks with **textbook examples** ("A man, a plan, a canal:
Panama" is *the* classic palindrome). Our hidden tests used the same textbook cases, so they *looked* leaked. That is
**contamination**, not leakage: tests built from famous examples overlap with what the model memorized. Fix applied:
each coding task's hidden tests now include unusual cases (e.g. `"Zq7!--7Q z"`) that a model cannot recite.

**Q: Add 5 hard tasks.** `hard-01` to `hard-05`; every expected answer was computed by a script, not by hand:

| Task | What makes it hard | Grader |
|------|--------------------|--------|
| hard-01 | messy CSV: duplicate ids, refunds, voids, 3 currencies → net USD per region | file JSON equality |
| hard-02 | interleaved log: pair START/END per request, ignore unfinished requests and stray ENDs (decoys) | exact |
| hard-03 | TTL + LRU cache with an injected clock (expiry boundary, expired-first eviction, update resets TTL) | hidden unit tests |
| hard-04 | two bugs in two files against a README spec; fixing only one still fails | hidden unit tests |
| hard-05 | batch report applying 3 ACME skill rules to 4 invoices (incl. the late-fee cap and a fiscal year boundary) | file JSON equality |

For hard-03 and hard-04, a reference solution passes, and the shipped buggy code (or a half-fix) fails.

**Q: "The statistical method, and how you chose N" — how do I answer this?**
*Method (30 seconds):*
1. The task is the unit, because trials of the same task are correlated.
2. Average the N trials of each task into a pass rate per config.
3. Take the per-task difference A − B: it's paired, so task difficulty cancels.
4. Bootstrap over tasks for a 95% CI of the mean difference.
5. If the CI is above 0, A is better; if it includes 0, "no significant difference", which the UI calls possible noise.

*How N was chosen:* by simulation, with `worker/power_analysis.py`. It runs the same method on fake experiments where
I know the truth:
- **No real difference** → it declares a winner ~5% of the time. That shows the method is **calibrated**.
- **Skills-sized effect** → detected 73% of the time at N=5 and 88% at N=10. The usual target is 80%, so **N=10** for the reported experiment.
- **Small effects** (+7 points on average) → only 64% even at N=10. More **tasks** is what helps; more trials does not.

*If pushed:*
- Why not McNemar? There is no natural pairing of trial *i* of A with trial *i* of B.
- Why not a z-test on all trials? Treating correlated trials as independent makes the CI too narrow.
- What does "no significant difference" mean? "Not detected at this power", not "equal".
