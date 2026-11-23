# Features, known limitations and roadmap

The platform evaluates **any agent harness running with any model** and answers *"did this change make the
agent better, worse, or the same, and how sure are we?"*

## Features

### What you can compare
- **Configs = harness × model × settings**: skills, memory, toolsets (`file`, `terminal`), temperature, step
  budget, token budget, timeout.
- **Harnesses included:** **Hermes Agent** (pinned commit) and a **baseline** tool-calling loop. New harnesses are
  adapters of about 50–150 lines ([GUIDE §5](GUIDE.md#5-add-a-harness)).
- **Models:** any OpenAI-compatible API (OpenRouter, xAI, Groq, Ollama, vLLM, LiteLLM), added as a YAML entry
  ([GUIDE §4](GUIDE.md#4-add-a-model)). Free OpenRouter models are included.
- **Versioned, immutable configs** (`name@vN`, content-hashed). Each config stores a snapshot of its model entry, so a
  past result always points to the exact settings that produced it.

### Tasks and grading
- **26 sample tasks** across reasoning, files, coding, skill-dependent, memory-dependent, writing, and 5 hard multi-step tasks (`hard-*`: messy data cleaning, interleaved log analysis, a TTL+LRU cache, a two-bug fix across files, and a skill-dependent batch report).
- **Deterministic graders:** exact match and regex (on an extracted `{"answer": ...}` field), file-state checks
  (text, number, JSON equality), and hidden unit tests written only after the agent finishes.
- **LLM-as-judge** with a rubric where every point must hold, reasoning before the verdict, temperature 0, and a
  5-vote majority with every vote stored.
- **Judge validation tooling:** agreement with human labels, Cohen's kappa, false-pass and false-fail counts, and an
  instability check.
- **Graders never reach the agent:** harness adapters receive the task without its grader.

### Fair comparisons
- **One place builds the request** (`Model.request_params`): every harness and the judge send the same temperature and
  provider routing, so two configs differ only in what the experiment varies.
- **Provider pinning** per model (`provider_routing` in `models.yaml`): full-precision weights only, providers that support
  every request field, no silent fallback. It is part of the config's model snapshot, so it is recorded with each result.
- **"Configs differ in"** on every comparison, with a warning when more than one setting differs.
- **Interleaved scheduling**: every config gets its repeat 0 of every task before any repeat 1, so the configs run at the same time.
- **Matched model pair** in `models.yaml`: Llama 3.1 8B vs Ministral 8B (same size, context and era).

### Execution
- **N repeated trials** per task and config, interleaved so that partial runs stay balanced.
- **Isolation:** one OS process per trial, plus a fresh temp workspace and a harness home (memory, skills).
  Memory crosses sessions only inside one trial, and only when enabled.
- **Reliability:**
  - bounded concurrency;
  - a hard timeout per trial;
  - exponential backoff with jitter on rate limits and 5xx;
  - provider failures (out of credits, bad key, unknown model) recorded as `error`, never graded;
  - atomic result writes;
  - automatic resume after a restart.
- **Control:** stop a whole experiment or one config; a circuit breaker stops a config whose first trials all fail;
  add a (fixed) config to an existing experiment; re-run an experiment exactly.

### Analysis
- **Pass rate per config** with a 95% bootstrap confidence interval over tasks.
- **Paired A-vs-B comparison:** mean per-task difference, 95% CI, bootstrap p-value, and a verdict that says
  "no significant difference" when the CI includes 0.
- **Per-task deltas** with their own CIs, sorted by size.
- **Cost, latency (mean, p90) and tokens** next to quality.
- **Failure categories:** `timeout`, `provider_error`, `step_exhaustion`, `loop`, `truncated`, `hallucinated_tool`,
  `bad_tool_args`, `tool_errors`, `gave_up`, `wrong_answer`. Definitions in
  [DESIGN.md §7](DESIGN.md#7-failure-analysis-workerfailurespy); also shown as tooltips in the UI.

### Observability
- **Full trajectory per trial:** messages, tool calls with arguments and results, tool errors, memory reads and
  writes, skills used, session boundaries and stop reasons, plus the grader's reasoning and the final score.
- **CLI** to run one prompt or task and print the trajectory; `--keep` keeps the trial folder for inspection.
- **Export:** JSON (everything, including traces) or CSV (one row per trial).

### Interfaces and deployment
- Web UI (Vue), REST API ([API.md](API.md)), CLI.
- One command: `docker compose up`, or `./scripts/run-local.sh` without Docker. SQLite storage, no other services.
- Tests: 21 Python tests (graders, harnesses, isolation, provider errors, failure rules, judge) and 12 API tests
  (statistics, versioning, runner retry/timeout/crash/resume/cancel/breaker).

---

## Known limitations

These are the places where results should not be trusted without care, or where the product is not ready for some uses.

### Validity of conclusions
- **Small sample task set.** About 20–26 tasks detect only large effects: in simulation, a +7-point average gain is found only 64% of the time even at N=10 (DESIGN §6). Real
  decisions need 100+ tasks drawn from your own workload.
- **Synthetic, short tasks.** Long multi-step work (refactors, research, browsing) is not covered.
- **Judge bias.** The judge is an LLM. It may prefer its own model family or longer answers, and hosted models are
  not deterministic even at temperature 0 (we observed one answer get 3 passes and 4 fails). Mitigated with explicit rubrics, 5-vote
  majority and validation. Only 19 labelled examples exist, written by one author, not independent annotators.
- **Contamination.** Generic tasks are surely in training data. Tasks based on made-up rules avoid this.
- **Harness differences.** Hermes has a large system prompt, its own retries and context compression, so its
  tokens and latency are not directly comparable with the baseline's. Hermes reads memory through its prompt;
  the baseline reads it with a tool call.
- **Errors are excluded, not imputed.** If provider errors cluster on one config, that comparison can be biased.
  Error counts are always shown.
- **Experiments that reuse results** (adding a config later) compare runs made at different times. Provider-side
  model changes in between are not controlled for.

### Security and isolation
- **The sandbox is not a security boundary.** Trials are separate processes in temp folders, inside one container
  (with Docker). With the `terminal` tool, and possibly file tools using absolute paths, an agent can read the task
  files (expected answers), the database, other running trials' folders, and the API key in its environment. This is
  fine for trusted agents and tasks; it is not fine for untrusted code, or for scores that must be cheat-proof.
- **Hermes can edit skills during a trial** (`skill_manage`). Edits are thrown away afterwards, but within a trial a
  skill can act as a notepad, even with memory off.
- **No authentication or multi-user support.** Anyone who can reach port 3000 can read results and start runs.

### Operations
- **Single machine.** An in-process queue with SQLite; not distributed and not built for parallel API instances.
- **Free models are rate-limited** per minute and per day. Retries happen at two layers (Hermes and the runner),
  which multiplies requests while rate-limited ([USAGE_AND_COST.md](USAGE_AND_COST.md#rate-limits)).
- **Served-by is not recorded per trial.** Routing is pinned by rule, but which provider actually answered each call is not
  stored. With `allow_fallbacks: false` and a single matching provider class, this is constrained but not proven.
- **Static prices.** Cost uses the prices in `models.yaml`; provider caching and discounts are ignored.
- **Adding a harness touches two lists:** the Python registry and `HARNESSES` in the API.
- **No live progress inside a trial.** You see a trial's trajectory only after it finishes.

---

## Roadmap

Ordered by value to a customer.

| Priority | Item | Why |
|----------|------|-----|
| 1 | **Container per trial** (no secrets, no task files mounted; model access through a key-holding proxy) | Makes scores cheat-proof and allows untrusted agents |
| 2 | **Bigger, customer-specific task sets** and a task-import format | Statistical power, and relevance to the customer's own workload |
| 3 | **Sequential testing / early stopping**: start at N=2 and add trials only where configs disagree | Cuts cost and time, often by half |
| 4 | **Authentication, projects and multiple users** | Needed for any shared deployment |
| 5 | **Postgres plus a distributed queue** | Scale beyond one machine |
| 6 | **Hierarchical bootstrap or mixed-effects model, with a correction for comparing many configs** | Statistics that hold when comparing many configs at once |
| 7 | **Independent judge labels** (2+ annotators, with their agreement reported) and a judge from a different model family | More trustworthy LLM grading |
| 8 | **Result cache** keyed by (config hash, task hash, seed), plus prompt caching | Lower cost for repeated runs |
| 9 | **Retry budget per experiment** and a shared rate limiter per provider | Avoids retry storms on rate-limited models |
| 10 | **Harness auto-discovery** (one registry instead of two lists), plus live streaming of trajectories | Smaller adapters, better debugging |
| 11 | **"Grader disagreement" failure category** (two graders per task) | Separates grader errors from agent errors |
