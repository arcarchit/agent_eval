# Design decisions: options, trade-offs, criteria

Use this to prepare for follow-up questions. For each decision: the options considered, their pros and cons,
what was chosen, and which criterion decided it. Before the interview, check that you agree with each choice.
If you would choose differently, say so and explain why. That is also a good answer.

## The criteria (in priority order)

| # | Criterion | What it means | Where it comes from |
|---|-----------|---------------|---------------------|
| C1 | **Valid evaluation** | The numbers must not lie: correct statistics, noise shown as noise, infra failures not counted as agent failures | "We care more about sound evaluation design than feature count" |
| C2 | **Small adapters** | A new harness or model is one file or one YAML entry; the core does not change | Explicit requirement |
| C3 | **Isolation and reliability** | No state leaks between trials; timeouts, rate limits and crashes never corrupt results | Explicit requirement |
| C4 | **Simple enough to explain and debug** | Fewer moving parts, plain code, every line defensible | 5-day time box, plus "Judgment: sensible scope cuts" |
| C5 | **One-command local run** | `docker compose up`, with keys in `.env` | Explicit requirement |

**Rule used for every choice:** take the simplest option that fully meets C1–C3, and write down what would make
you switch to the bigger option. "When would you change this?" is the most likely follow-up question.

---

## 1. Boundary between TypeScript and Python

| Option | Pros | Cons |
|--------|------|------|
| **A. Spawn one Python process per trial (stdin JSON → stdout JSON)** ✅ | Isolation for free (own memory, own env vars, own cwd); hard timeout with SIGKILL; a crash affects one trial; the contract is 3 things (job in, result out, exit code) | ~1–2 s start-up per trial; no warm model clients; no streaming progress inside a trial |
| B. Long-running Python HTTP service (FastAPI) | No start-up cost; can stream progress | Trials share one process, so Hermes' global state (`HERMES_HOME` env var, cwd) leaks between concurrent trials; killing a hung trial is hard; one more service |
| C. Queue (Redis + BullMQ, Python consumers) | Scales to many machines; durable queue | Two more components; overkill for local single-user |
| D. Everything in Python (FastAPI + Python stats) | One language | Violates the "TypeScript for API" requirement |

**Chosen: A.** Criteria **C3 + C4**. Hermes sets `HERMES_HOME` and the cwd globally, so concurrent trials in one
process would interfere. A process per trial makes isolation structural instead of something you must remember
to get right. Start-up cost is small next to 5–60 s of LLM latency.
**Would switch to C** for many machines or thousands of trials per run, but keep process-per-trial inside each consumer.

## 2. Job execution and queue

| Option | Pros | Cons |
|--------|------|------|
| **A. In-process pool in NestJS, state in SQLite** ✅ | No infrastructure; the DB is the queue (`status = pending`); resume = reset `running` → `pending` at startup | One machine; no priorities or fairness between experiments |
| B. BullMQ + Redis | Retries, rate limiting and dashboards built in | Extra service; retry state lives in Redis *and* the DB, so two sources of truth |
| C. Temporal or another workflow engine | Very robust | Heavy; a lot to explain |

**Chosen: A.** **C4 + C5**, while still meeting **C3**: retries, backoff, timeout and resume are about 60 lines in `runner.service.ts`.
**Would switch** when there are several API instances or users.

## 3. Persistence

| Option | Pros | Cons |
|--------|------|------|
| **A. SQLite via Node's built-in `node:sqlite`, raw SQL** ✅ | One file; no server; no native build step; the SQL is visible | Marked "experimental" in Node 22; single writer; traces are stored as JSON text |
| B. Postgres + TypeORM or Prisma | Production standard; concurrent writers; migrations | Another container; ORM magic to explain |
| C. `better-sqlite3` | Mature, fast | Native module, can fail to build on some machines or Docker images |
| D. JSON files on disk | Simplest of all | No queries, no atomic updates; the comparison needs joins |

**Chosen: A.** **C4 + C5**. Each trial result is written in one UPDATE, so there is no half-written state (**C3**).
**Would switch to B** for multiple users, or when traces get large (traces could move to object storage).

## 4. Isolation and sandbox

| Option | Pros | Cons |
|--------|------|------|
| **A. Temp dir + separate process + timeout** ✅ | Fast; enough to stop state leaks between trials | **Not a security boundary**: Hermes' terminal tool can reach the container filesystem |
| B. Docker container per trial | Real filesystem and network isolation | Docker-in-Docker; slow start; complicates the one-command setup |
| C. microVM / hosted sandbox (Firecracker, E2B) | Strongest isolation | External service or a lot of infrastructure |

**Chosen: A** for this trusted, self-written task set. **C3** asks for *no state leaks*, which A gives. Security
against malicious code was not required. The limit is written down in the design doc.
**Would switch to B or C** for untrusted tasks or user-submitted code.

## 5. Model access (adapter for "Y")

| Option | Pros | Cons |
|--------|------|------|
| **A. OpenAI-compatible API (OpenRouter); a model = a YAML entry** ✅ | Adding a model needs no code; one key covers many providers; Hermes speaks this API natively | Depends on OpenRouter; provider-specific features (e.g. Anthropic prompt caching) only where OpenRouter passes them through |
| B. LiteLLM proxy | Self-hosted; any provider | Another container to run |
| C. One SDK adapter class per provider | Full control | More code, and every harness has to support every adapter |

**Chosen: A.** **C2** (a new model is *config*, not code) and **C4**. Because the endpoint is just a `base_url`,
B can be added later without code changes: point `base_url` at a LiteLLM proxy.

## 6. How to integrate Hermes

| Option | Pros | Cons |
|--------|------|------|
| **A. Python library: `AIAgent(...).run_conversation()`** ✅ | Returns the full message list, token counts and exit reason, so a rich trace with no parsing | Couples us to Hermes' Python API (mitigated by pinning the commit) |
| B. Run the `hermes` CLI as a subprocess | Loose coupling | Would have to parse terminal output; trace detail is lost |
| C. Hermes' own `batch_runner.py` | Built for batch runs | Its own output format, memory off by design, hard to inject per-trial settings |

**Chosen: A.** **Observability** (an evaluation area in the assignment) plus **C1**: you cannot explain *why* a
trial failed without the full trajectory. Verified with a fake model server: memory written in session 1 shows
up in the system prompt of session 2.

## 7. Second harness

| Option | Pros | Cons |
|--------|------|------|
| **A. Hand-written baseline tool loop (~100 lines)** ✅ | Fully understood; same capabilities as Hermes (files, shell, memory, skills); a useful "how much does the harness add?" reference | Not a "real" framework |
| B. Claude Agent SDK / OpenAI Agents SDK | Real, popular frameworks | Tied to one provider, which breaks "any model" |
| C. LangGraph or smolagents | Model-agnostic | Another large dependency; more to explain |

**Chosen: A.** **C2**: it proves the interface is not shaped around Hermes. **C4**: you can explain every line.
The assignment explicitly allows "a minimal baseline you write yourself".

## 8. Statistical test (the most important one, C1)

| Option | Pros | Cons |
|--------|------|------|
| **A. Paired bootstrap over tasks (CI of mean per-task difference)** ✅ | Respects that trials of one task are correlated (tasks are the clusters); paired, so task difficulty cancels out; no normality assumption; easy to explain | With few tasks (~20) the percentile CI is a bit too narrow; it ignores trial-level noise inside the per-task means (that noise is in the means, but the means are treated as fixed) |
| B. McNemar | Classic paired test for binary outcomes | Needs one pair per item. With N trials per task there is no natural pairing (trial 3 of A has no link to trial 3 of B); throwing away N−1 trials loses power |
| C. Two-proportion z-test or Wilson CI on all trials | Very simple | Treats 105 trials as independent, so CIs are **falsely narrow**: the classic eval mistake |
| D. Hierarchical bootstrap (resample tasks, then trials within each task) | Captures both noise sources | More code, little extra value at N=5 |
| E. Mixed-effects logistic regression or Bayesian | Most principled | Hard to explain; needs a stats library in TypeScript |

**Chosen: A**, plus per-task CIs and a verdict that says "no significant difference" whenever the CI contains 0.
Criterion **C1 + C4**. Know the weakness: say D or E would be the upgrade, and that with about 20 tasks a
**bigger task set** matters more than a fancier test.

**N = 5:** the trial noise on a task's pass rate is at most 0.5/√N ≈ 0.22, and averaging over 21 tasks makes it
small next to the differences between tasks. The minimum detectable effect is about
2·SD(d)/√tasks ≈ 13 points. More tasks help more than more trials.

## 9. What counts as a failure

| Situation | Options | Chosen | Why |
|-----------|---------|--------|-----|
| Rate limit / 5xx | count as fail · exclude · retry | **Retry with backoff ×4, then mark `error` and exclude** | Counting outages as fails would make a config look worse because of the provider (**C1**) |
| Timeout | exclude · count as fail | **Count as fail (`timeout`)** | Being too slow is agent behaviour, and excluding it would hide slow configs |
| Crash in our code | fail · exclude | **`error`, excluded, shown as a count** | Not the agent's fault |

Risk to mention: if errors cluster on one config, excluding them biases the comparison. The UI shows error
counts per config so this stays visible.

## 10. Score type

| Option | Pros | Cons |
|--------|------|------|
| **A. Binary pass/fail** ✅ | Clear; easy statistics; judge verdicts are more reliable than scales | Partial credit is lost |
| B. Continuous 0–1 or a 1–5 judge scale | Finer signal | Judges disagree with each other on scales; scales from different graders are not comparable |

**Chosen: A** (**C1**). A `score` field is still stored, so continuous scores can be added later.

## 11. LLM judge design

| Choice | Options | Chosen and why |
|--------|---------|----------------|
| Output | 1–10 score · pass/fail | **Pass/fail where *all* rubric points must hold.** Binary verdicts agree with humans more often. |
| Reasoning | verdict only · reasoning first | **Reasoning before the verdict**, in JSON. Better accuracy, and it is shown in the UI as the "why". |
| Determinism | default · temperature 0 | **Temperature 0** |
| Validation | trust it · compare with labels | **18 labelled answers, accuracy + Cohen's kappa; trust only if kappa ≥ 0.8.** Kappa corrects for chance agreement: 90% accuracy on a set that is 90% fails is worthless. |
| Use | everywhere · only where needed | **Only 3 of 21 tasks.** Deterministic graders wherever possible: cheaper, and no bias. |

Weakness to admit: the labels are the author's, and the judge model may be from the same family as the agent (self-preference).

## 12. Config versioning

| Option | Pros | Cons |
|--------|------|------|
| **A. Immutable rows, `name@vN`, content hash** ✅ | A past result always points to exact settings; identical settings are de-duplicated | Many versions over time |
| B. Mutable config with an audit log | Fewer rows | Results can silently point to changed settings |
| C. Configs as files in git | Diffable | Results need a commit SHA to be reproducible; harder in the UI |

**Chosen: A** (**C1**, reproducibility). Experiments also store a **snapshot of the tasks**, and Hermes is pinned
to a commit, so every input to a result is frozen.

## 13. Task format

| Option | Pros | Cons |
|--------|------|------|
| **A. One YAML file per task** ✅ | Adding a task = adding a file, with no code; reviewable in pull requests | Validation only at run time (a test checks all tasks parse) |
| B. Tasks stored in the DB, edited in the UI | Editable without git | Not versioned; harder to review |
| C. Python classes (like many eval frameworks) | Unlimited flexibility | Adding a task becomes coding |

**Chosen: A** (**C2 + C4**).

## 14. Memory semantics

| Option | Pros | Cons |
|--------|------|------|
| **A. Memory is shared only *within* a trial: `memory_setup` sessions run first, then the prompt in a fresh session** ✅ | Trials stay independent (valid statistics); memory's effect is measurable inside one trial | Does not test long-term memory across many tasks |
| B. Memory persists across trials of a config | More realistic for a long-lived agent | Trials depend on run order, so they are not independent, which breaks the bootstrap; results depend on scheduling |

**Chosen: A** (**C1 + C3**). The assignment says "no state leaks unless a memory setting explicitly says so".
Here memory is explicit and contained.

## 15. Failure categories

| Option | Pros | Cons |
|--------|------|------|
| **A. Ordered rules over the trace** ✅ | Deterministic, free, easy to explain and test | Coarse; the first matching rule wins |
| B. LLM classifier over the trajectory | Richer categories | Costs money; is itself an unvalidated judge |

**Chosen: A** (**C4**). Stretch goal; "grader disagreement" is listed as not done.

## 16. Which experiment

| Option | Pros | Cons |
|--------|------|------|
| **A. Hermes skills on vs off (same model)** ✅ | Clear hypothesis; the tasks were designed with made-up rules the model cannot know, so the effect is causal and large | Somewhat "designed to succeed" |
| B. Two models in the same harness | Very common real question | Many small differences, so it may land in "noise" with 21 tasks |
| C. Hermes memory on vs off | Shows memory | Only 2 memory tasks: too few to detect anything |

**Chosen: A** because it can produce a clear result with 21 tasks (**C1**: the effect is larger than the
detectable effect of about 13 points). If asked: B is the natural next experiment, and needs more tasks.

## 17. UI

| Option | Pros | Cons |
|--------|------|------|
| **A. Plain Vue 3 + router, hand-written CSS, served by NestJS** ✅ | Small; one port; no component library to learn | Basic look |
| B. Vuetify or another component library | Polished | Bundle size and API surface |

**Chosen: A.** "A functional UI is enough." Effort went into what the UI *says*: CI, verdict, noise warning, cost next to quality, and the trajectory view.

---

## Likely "what would you do with more time?" answers
1. More tasks (100+), harder and multi-step: this is the biggest lever for statistical power.
2. A container per trial, for untrusted code.
3. Sequential testing: start with N=2 and add trials only where configs disagree, which cuts cost.
4. A hierarchical bootstrap or a mixed-effects model, and Holm or FDR correction when comparing many config pairs.
5. Independent human labels for the judge (2 annotators, with their agreement reported), and a judge from a different model family than the agent.
6. Prompt caching and a result cache keyed by (config hash, task hash, seed).
7. Postgres and a real queue for multiple users.
