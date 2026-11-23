# Submission: Agent Evals Platform (Applied AI Engineer take-home)

This page follows the assignment's **Deliverables** list in order. Each section gives the short answer and links to
the full document. A reviewer can read only this page and drill down where interested.

| # | Deliverable | Where | Status |
|---|-------------|-------|--------|
| 1 | Source code + README (setup, add a task / harness / model) | repo root, [README](../README.md), [GUIDE](GUIDE.md) | done |
| 2 | Design document | [DESIGN.md](DESIGN.md) (+ [JUDGE_VALIDATION.md](JUDGE_VALIDATION.md)) | done |
| 3 | Known limits | [FEATURES.md → Known limitations](FEATURES.md#known-limitations) | done |
| 4 | Cost estimate (formula + actual spend + how to reduce) | [USAGE_AND_COST.md](USAGE_AND_COST.md) | done (actual spend $0.0705 for 260 trials) |
| 5 | One real experiment | [EXPERIMENT.md](EXPERIMENT.md) | done: Llama 3.3 70B vs Ministral 8B, no significant difference |
| 6 | Video (≤ 10 min) | script below | **TODO: record** |

---

## 1. Source code

- **Stack**: NestJS API + SQLite (TypeScript), Vue 3 UI (TypeScript), Python worker for harnesses (Hermes is a Python library).
- **Run**: `docker compose up --build` (keys in `.env`), or `./scripts/run-local.sh` without Docker.
- **README** covers setup. The step-by-step tutorials are in [GUIDE.md](GUIDE.md): [add a task](GUIDE.md#3-add-a-task),
  [add a model](GUIDE.md#4-add-a-model), [add a harness](GUIDE.md#5-add-a-harness) (with a complete, tested example adapter).
- **Tests**: 23 Python (graders, harnesses, isolation, grader secrecy, provider errors, failure rules, truncation, judge voting, identical request fields across harnesses) and
  13 API (statistics incl. per-category comparison, config versioning, runner retry/timeout/crash/resume/cancel/circuit breaker). They need no API key.
- **Task set**: 26 tasks with mixed graders: exact/regex 12, file-state 5, hidden unit tests 6, LLM judge 3 (these overlap the
  categories below).
  - Skill-dependent: 6 (made-up ACME rules no model can know).
  - Memory-dependent: 2.
  - Hard multi-step: 5.
- **Harnesses**: Hermes Agent (pinned commit) and a hand-written baseline tool loop.
- **Models**: any OpenAI-compatible API via `models.yaml` (OpenRouter by default; gpt-4o-mini, Llama 3.3 70B, free models; Grok, Groq and Ollama examples).
- **API reference**: [API.md](API.md).

## 2. Design document → [DESIGN.md](DESIGN.md)

| Required topic | Section | One-line answer |
|----------------|---------|-----------------|
| Architecture | [§1](DESIGN.md#1-architecture) | Vue → NestJS (state, scheduling, statistics) → **one Python process per trial** (harness + grader). Contract: job JSON in on stdin, result JSON out on stdout, plus an exit code. |
| Harness and model adapter interfaces | [§2](DESIGN.md#2-adapter-interfaces) | `Harness.run(task, config, model, workspace, home) -> Trace`. A model is a `models.yaml` entry (OpenAI-compatible). A new X or Y is one class or one YAML entry. |
| Data model | [§3](DESIGN.md#3-data-model-sqlite-apisrcdbts) | `configs` (immutable `name@vN`, content hash, model snapshot), `experiments` (task snapshot), `trials` (status, grade, trace, tokens, cost, latency, LLM calls, turns). |
| Isolation and reproduction | [§4](DESIGN.md#4-isolation-and-reproducibility) | Fresh process + temp workspace + temp harness home per trial. Memory crosses sessions only inside one trial, and only if enabled. Graders never reach the agent. Rerun = same config versions on the same task snapshot. |
| Grader design and validation | [§5](DESIGN.md#5-graders-and-how-they-were-validated), [JUDGE_VALIDATION.md](JUDGE_VALIDATION.md) | Deterministic graders where possible. JSON answer extraction. Hidden unit tests are written after the agent finishes. LLM judge: all-points rubric, 5-vote majority, validated with accuracy, Cohen's kappa and an instability check. A real instability (3 passes / 4 fails on the same answer) was found; the validation then showed the judge still misses the bar (kappa 0.68 < 0.8; it misses banned words 5/5 times), so judge tasks are reported separately. |
| Statistical method and how N was chosen | [§6](DESIGN.md#6-statistical-method) | Task is the unit of analysis; paired per-task differences; bootstrap CI over tasks; the verdict says "no significant difference" when the CI contains 0. **N chosen by simulating our own method**: false positive rate ≈ 5% (calibrated); power for a skills-sized effect is 73% at N=5 and 88% at N=10, so **N=10** for the reported experiment. |

## 3. Known limits → [FEATURES.md](FEATURES.md#known-limitations)

Where conclusions should **not** be trusted:
- **Task-set size.** About 20–26 tasks detect only large effects. A +7-point average gain is found only 64% of the time even at N=10.
- **Synthetic, short tasks.** Long agentic work is not covered.
- **Judge bias and instability.** The judge is an LLM, and the reference labels come from one author.
- **Contamination.** Generic tasks are surely in training data; that is why the skill tasks use made-up rules.
- **Harness quirks.** Hermes' large prompt, its own retries and context compression, and the fact that it can edit skills
  mid-trial make its tokens and latency not directly comparable with the baseline's.
- **Sandbox is not a security boundary.** An agent with the terminal tool could read the task files.
- **Errors are excluded, not imputed.**
- **Experiments that add a config later** span different times.

## 4. Cost estimate → [USAGE_AND_COST.md](USAGE_AND_COST.md)

```
cost = tasks × trials × configs × (avg_input_tokens × price_in + avg_output_tokens × price_out)
     + judge_tasks × trials × configs × judge_votes × (judge_in × price_in + judge_out × price_out)
```
**Measured** (Hermes + gpt-4o-mini, 94 trials):
- 2.5–3 LLM calls per trial;
- ~1.7–1.9k input tokens per call, ~120 output tokens per trial;
- **$0.0007–0.0009 per trial**;
- a judge call costs ≈ $0.0001.

**Actual spend (reported experiment `33037e1d`, 26 tasks × N=5 × 2 configs = 260 trials):** models $0.0680 + judge
$0.0025 = **$0.0705**, about $0.00027 per trial. The discarded first attempt cost about $0.08.

**How to reduce it:** prompt caching of the harness system prompt; early stopping (N=2 first, then add trials only
where configs disagree); task sampling for quick checks; a result cache for identical trials; deterministic graders
(23 of 26 tasks make 0 grading calls); fewer judge votes once the judge is shown to be stable.

## 5. One real experiment → [EXPERIMENT.md](EXPERIMENT.md)

- **Single axis:** the model. Baseline harness, file + terminal tools, both skills, memory off, temperature 0,
  10 steps, 1,024 tokens. A = Llama 3.3 70B, B = Ministral 8B.
- **Hypothesis** (stated honestly as not pre-registered): the 70B model passes more tasks and costs more.
- **Setup:** 26 tasks × N=5 × 2 configs = 260 trials, 0 errors.
- **Result:** A 28.5% [13.1, 45.4] vs B 35.4% [20.0, 53.1]; A − B = −6.9 points, 95% CI [−23.1, +9.2], p ≈ 0.41 →
  **no significant difference** (same verdict without the 3 judge tasks, whose grades were mostly judge parse failures).
- **Trajectories:** Llama writes tool calls and code as chat text instead of calling tools (`code-01`, `code-04`,
  `hard-01`); Ministral reads the ACME rule but builds a 4-letter customer code (`skill-01`) and does mental
  arithmetic wrong (`arith-01`).
- **Conclusion:** hypothesis not supported; the models fail in different ways; Ministral costs 60% more per trial.

## 6. Video script (≤ 10 min)

A script to read aloud. *Italic lines* are what to show on screen; the rest is what to say. About 1,000
spoken words, roughly 7½ minutes at a calm pace, leaving 2 minutes for clicking and pauses.

**Before recording:**
- Run `docker compose up -d --build`, so new trials show the system prompt and truncation warnings.
- Don't edit `models.yaml` while recording: the single-file mount breaks the UI until a restart.
- Open these tabs:
  - experiment `33037e1d`;
  - trials `33037e1d-4`, `33037e1d-5` and `33037e1d-41`;
  - `docs/JUDGE_VALIDATION.md`;
  - `docs/DESIGN.md` §6.
- For the live run, pick 3–4 tasks without `judge-*` or `skill-05` (for example `arith-01`, `code-01`,
  `skill-01`, `file-01`), N=2, model gpt-4o-mini.

### 0:00 – 0:45 · The problem

*Show: README, then the architecture diagram in DESIGN.md §1.*

> An AI agent has two parts: a harness, meaning the loop, tools, skills and memory, and a model. Either can
> change at any time. Every change raises the same question: did the agent get better, worse, or stay the
> same, and how sure are we?
>
> This platform answers that with evidence. The UI is Vue, the API is NestJS, and every trial runs as its own
> Python process, so trials can't leak state into each other. There are two harnesses: Hermes Agent and a
> plain tool-calling baseline I wrote. Models are entries in one YAML file, reached through OpenRouter.

### 0:45 – 2:00 · Define a config

*Show: Configs page. Create a config: Hermes, gpt-4o-mini, file tools, the `acme-billing` skill. Save. Change
the temperature, save again under the same name.*

> A config is harness times model times settings: skills, memory, tools, temperature, and step and token
> budgets. I'll create one with Hermes, gpt-4o-mini and the ACME billing skill.
>
> Configs are immutable. If I change a setting and save under the same name, I get version 2, and version 1 is
> untouched. Each config also stores a snapshot of the model's settings, so a result from last week can
> always be traced back to exactly what produced it.

### 2:00 – 3:00 · Run it

*Show: Experiments page. Select the new config and one other, the 3–4 tasks, N=2. Click Run. Let progress
tick.*

> Now I'll run it. Agents are random, so every task runs N times per config. Here I use 2 to keep the demo
> short; real comparisons use 5 or 10.
>
> Each trial gets a fresh process, a fresh workspace and a fresh harness home. Memory only carries across
> sessions inside one trial, and only if the config turns it on. Rate limits are retried with backoff. If the
> provider fails, for example no credits, the trial is marked as an error and excluded. It is never counted as
> a wrong answer. While this runs, let me show a finished experiment.

### 3:00 – 5:30 · Compare results

*Show: experiment `33037e1d`. Point at the summary table, then the verdict, then the per-task table. Hover a
failure category.*

> This experiment changes exactly one thing: the model. Same baseline harness, same tools and skills,
> temperature zero. Llama 3.3 70B against Ministral 8B, on 26 tasks, 5 trials each: 260 trials, no errors.
>
> Llama passed 28.5 percent, Ministral 35.4. But look at the confidence intervals: they overlap a lot. The
> paired difference is minus 6.9 points, with a 95 percent interval from minus 23 to plus 9. That interval
> includes zero, so the verdict is "no significant difference". The smaller model scoring higher here is noise
> at this sample size.
>
> How is that computed? The unit is the task, not the trial. Five trials of the same task are correlated, so
> treating 130 trials as independent would make the intervals look far too certain. I average each task's
> trials, take the per-task difference between the two models, and bootstrap over tasks.
>
> Quality isn't the only column. Cost, latency and tokens sit right next to it. Ministral costs about 60
> percent more per trial, because it uses more tokens and has a higher input price.
>
> The per-task table shows where the difference comes from, and it's not one-sided. Ministral wins the coding
> tasks; Llama wins the invoice-ID task and the arithmetic task. Hovering a failure category explains it.
> Most failures here are "wrong answer", and that label hides something interesting. Let's open one.

### 5:30 – 7:30 · Drill into a failed trial

*Show: trial `33037e1d-4` (Llama, code-01). Scroll the trajectory. Then `33037e1d-5`. Then `33037e1d-41`.*

> This is Llama failing a coding task: write a palindrome function into `solution.py`. Look at its reply. The
> function is correct, but Llama wrote the tool call as plain text. It never actually called `write_file`, so no
> file was created, and the hidden unit test fails with "no module named solution". It did this in all five
> trials.
>
> Here's Ministral on the same task: a real tool call, the file is written, the tests pass. So Llama's
> weakness here isn't coding. It describes the action instead of taking it.
>
> And Ministral's own failure, on the invoice task: it reads the ACME billing skill, and you can see the skill
> being used right here. Then it builds the customer code with four letters instead of three. It read the
> rule and applied it wrong.
>
> Every trial shows this level of detail: the system prompt, each model call with its timing, tool calls and
> results, skills used, memory reads and writes, the grader's reasoning, and a failure category. You can see
> why it passed or failed, not just that it did.

*If the live run has finished: open one of its trials and point at the system-prompt event.*

### 7:30 – 8:45 · Can we trust the judge?

*Show: `docs/JUDGE_VALIDATION.md`, the results table.*

> Three tasks are graded by an LLM judge, so I had to check the judge itself. I hand-labelled 19 answers and
> measured agreement with Cohen's kappa, which corrects for agreement you'd get by chance. My bar was 0.8.
>
> No judge passed. The best, gpt-4o-mini, reached 0.68. And the way it failed matters: twice it said a banned
> word was absent when it was right there, five votes out of five. Majority voting fixes random errors, not
> consistent ones. The lesson: literal rules, like "this word must not appear", belong in a deterministic
> check, not a judge. Until that's fixed, I report the experiment with and without the judge tasks. Here the
> verdict is the same either way.

### 8:45 – 10:00 · Limits and what's next

*Show: the power table in DESIGN.md §6, then FEATURES.md known limitations.*

> Where shouldn't you trust these conclusions? First, size. I chose N by simulating my own method on fake
> experiments where the truth is known. With 26 tasks and 5 trials, a large effect is detected about 73
> percent of the time, and small effects are often missed. So "no significant difference" means "not detected
> at this power", not "equal".
>
> Second, the judge is below the bar, as we just saw. Third, providers weren't pinned, so tool-calling
> behaviour can depend on which provider served the request. And the sandbox isolates state, but it isn't a
> security boundary.
>
> Next I'd pin providers, add a failure category for tool calls written as text, move literal rules out of
> the judge, and grow the task set, because past about ten trials, more tasks is what buys power.
>
> Thanks for watching.

## How the evaluation criteria are covered

| Area | Evidence |
|------|----------|
| **Eval methodology** | Task-level paired bootstrap; calibration and power checked by simulation; errors excluded, not counted as failures; validated judge with an instability check; hidden graders |
| **Abstraction** | `Harness` interface (2 adapters + a tested tutorial adapter); models as config; the core never changes |
| **Observability** | Full trajectory with per-step timing, tool calls and results, memory reads/writes, skills used, session boundaries, grader reasoning, failure category; CLI with `--keep` |
| **Engineering quality** | 36 tests; resume after restart; atomic writes; retries with backoff; timeouts; circuit breaker; 0 `npm audit` vulnerabilities; one-command run |
| **Judgment** | Scope cuts listed in [internal/DECISIONS.md](internal/DECISIONS.md); limits in FEATURES; first-run bugs found and documented honestly |

## Before submitting (checklist)

- [x] Judge validation run; results (below the acceptance bar) written up in JUDGE_VALIDATION.md.
- [x] Real experiment written up with results, CIs, trajectories, conclusion and actual spend (EXPERIMENT.md).
- [ ] Rebuild the container (`docker compose up -d --build`) so the UI shows the system prompt, truncation warnings and failure tooltips.
- [ ] Record the video, following the script above.
- [ ] Merge the branch into `main`, so the reviewer sees everything on the default branch.
- [ ] Decide whether to share `docs/internal/` (interview prep notes). Consider removing `internal/PLAN.md` and `internal/LEARNING_NOTES.md` from the submitted repo.
