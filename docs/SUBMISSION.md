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
  12 API (statistics, config versioning, runner retry/timeout/crash/resume/cancel/circuit breaker). They need no API key.
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

| Time | Show | Say |
|------|------|-----|
| 0:00–0:45 | README, architecture diagram (DESIGN §1) | The problem; harness × model; one process per trial for isolation |
| 0:45–2:00 | **Configs page**: create `hermes-skills` and `hermes-noskills`; show `@v1`, then change a setting to get `@v2` | Configs are immutable and versioned; the model snapshot means past results stay reproducible |
| 2:00–3:00 | **Experiments page**: select both configs, N, tasks, click Run; progress updates | Interleaved trials; concurrency, retries, timeouts; provider errors are never graded; Stop / circuit breaker |
| 3:00–5:30 | **Experiment page**: pass rate + CI, cost/latency/tokens/LLM calls, verdict, per-task deltas, failure categories | Paired bootstrap over tasks; "no significant difference" when the CI includes 0; where the difference comes from |
| 5:30–8:00 | **Drill into a failed trial**: per-task cell → trial → trajectory with step timings, tool calls, skill_used / memory events, grader reasoning | Why it failed (e.g. skills off: the model guessed the ACME invoice format); compare with a passing trial from the other config |
| 8:00–9:00 | `docs/JUDGE_VALIDATION.md` | The same answer got 3 passes / 4 fails; rubric fix + 5-vote majority + instability metric |
| 9:00–10:00 | FEATURES known limitations + power table | What not to trust; why N=10; what I would build next (container per trial, bigger task set, early stopping) |

## How the evaluation criteria are covered

| Area | Evidence |
|------|----------|
| **Eval methodology** | Task-level paired bootstrap; calibration and power checked by simulation; errors excluded, not counted as failures; validated judge with an instability check; hidden graders |
| **Abstraction** | `Harness` interface (2 adapters + a tested tutorial adapter); models as config; the core never changes |
| **Observability** | Full trajectory with per-step timing, tool calls and results, memory reads/writes, skills used, session boundaries, grader reasoning, failure category; CLI with `--keep` |
| **Engineering quality** | 35 tests; resume after restart; atomic writes; retries with backoff; timeouts; circuit breaker; 0 `npm audit` vulnerabilities; one-command run |
| **Judgment** | Scope cuts listed in [internal/DECISIONS.md](internal/DECISIONS.md); limits in FEATURES; first-run bugs found and documented honestly |

## Before submitting (checklist)

- [x] Judge validation run; results (below the acceptance bar) written up in JUDGE_VALIDATION.md.
- [x] Real experiment written up with results, CIs, trajectories, conclusion and actual spend (EXPERIMENT.md).
- [ ] Rebuild the container (`docker compose up -d --build`) so the UI shows the system prompt, truncation warnings and failure tooltips.
- [ ] Record the video, following the script above.
- [ ] Merge the branch into `main`, so the reviewer sees everything on the default branch.
- [ ] Decide whether to share `docs/internal/` (interview prep notes). Consider removing `internal/PLAN.md` and `internal/LEARNING_NOTES.md` from the submitted repo.
