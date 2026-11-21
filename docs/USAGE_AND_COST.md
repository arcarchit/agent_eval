# Usage and cost: LLM calls, tokens, turns and money per experiment

Numbers marked **measured** come from 94 real trials (Hermes + `gpt-4o-mini`, experiment `e181b969`).
Everything else is an estimate from the formulas below. Your exact numbers are on the experiment page (tokens, cost)
and in the CSV export.

## Vocabulary

| Term | Meaning |
|------|---------|
| **Trial** | one task, run once, under one config |
| **Session** | one fresh conversation. A trial has 1 session, or 2+ for tasks with `memory_setup` (`mem-01`, `mem-02`) |
| **Turn** (user turn) | one user message. One per session, so 1 per trial (2 for memory tasks) |
| **Step** = **LLM call** | one request to the model. Inside a session the agent loops: call the model, run tools, call the model again… until it answers or hits `max_steps` |
| **Judge call** | one request to the judge model, for `llm_judge` tasks only |

## 1. Agent calls (the harness)

| | Skills off | Skills on | Source |
|---|---|---|---|
| LLM calls per trial | 2.5 on average (min 1, max 11) | 2.9 on average (max 11) | measured |
| Input tokens per call | ~1,700 | ~1,900 | measured; this is mostly Hermes' system prompt and tool schemas, resent on every call |
| Input tokens per trial | ~4,100 | ~5,600 | measured |
| Output tokens per trial | ~110 | ~125 | measured |
| Cost per trial (gpt-4o-mini) | $0.0007 | $0.0009 | measured |
| Latency per trial | 6.4 s | 7.4 s | measured |

Why input tokens dominate: every call resends the whole conversation so far (system prompt, task, earlier tool
calls and results). A trial with *k* calls therefore costs roughly *k* × (system prompt) + the growing history.
Skills on costs more because of the skills index in the prompt and the extra `skill_view` call.

Bounds per trial: at least 1 call; at most `max_steps` (10) per session, so 20 for a 2-session memory task.
Hermes may add an occasional extra call of its own (for example, to compress context in long runs). Its
internal retries on errors also count as requests.

## 2. Grading calls

| Grader | Runs per trial | LLM calls per trial |
|--------|----------------|---------------------|
| `exact`, `regex`, `file_check` | once | **0** |
| `unit_test` | once (a local Python process) | **0** |
| `llm_judge` | once, which asks the judge `judge_votes` times (5) and takes the majority | **5** |

So yes: for judge tasks, the **same response is judged 5 times**, on purpose, because the judge is not
deterministic even at temperature 0 (see `docs/internal/JUDGE_VALIDATION.md`). Each judge call is a single turn
with no tools: about **400 input tokens** (task + answer + rubric + instructions) and **60–150 output tokens**
(reasoning + verdict). That is about **$0.0001 per call** with gpt-4o-mini (measured: $0.000096 per call).
In the sample task set, 3 of 21 tasks use the judge (`skill-05`, `judge-01`, `judge-02`).

## 3. Formulas

```
trials          = tasks × N × configs
agent_calls     = trials × avg_calls_per_trial                         (≈ 2.5–3 for Hermes)
judge_calls     = judge_tasks × N × configs × judge_votes
total_requests  = agent_calls + judge_calls  (+ retries; see Rate limits)

agent_tokens_in  = trials × avg_input_per_trial                        (≈ 4–6k)
agent_tokens_out = trials × avg_output_per_trial                       (≈ 110–125)
judge_tokens_in  = judge_calls × ~400
judge_tokens_out = judge_calls × ~100

cost = (agent_tokens_in × price_in + agent_tokens_out × price_out) / 1e6     (agent model prices)
     + (judge_tokens_in × price_in + judge_tokens_out × price_out) / 1e6     (judge model prices)

time ≈ trials × avg_latency / MAX_CONCURRENCY      (if not rate-limited)
```

## 4. Worked example: the skills experiment

19 tasks (without `mem-*`) × N=5 × 2 configs, Hermes, gpt-4o-mini, judge gpt-4o-mini with 5 votes.

| Quantity | Calculation | Result |
|----------|-------------|--------|
| Trials | 19 × 5 × 2 | **190** |
| Agent LLM calls | 190 × ~2.7 | **~510** |
| Judge calls | 3 judge tasks × 5 × 2 × 5 votes | **150** |
| Total requests | 510 + 150 | **~660** (before retries) |
| Agent input tokens | 190 × ~4,900 | ~930k |
| Agent output tokens | 190 × ~117 | ~22k |
| Judge tokens | 150 × (400 in + 100 out) | ~60k in, ~15k out |
| **Cost** (gpt-4o-mini, $0.15 / $0.60 per 1M) | 0.93M × 0.15 + 0.022M × 0.6 + 0.06M × 0.15 + 0.015M × 0.6 | **≈ $0.17** |
| Time (paid model, `MAX_CONCURRENCY=4`) | 190 × ~7 s / 4 | **~6 min** |
| Time (free model, ~20 requests/min) | 660 requests / 20 | **≥ 33 min**, and more with retries |

The full 21 tasks give 210 trials and about the same totals (memory tasks have 2 sessions, so a few more calls).

## 5. Judge validation script

`python worker/judge_validation.py [--votes N]`

| Quantity | Default (`judge_votes: 5`) | `--votes 1` |
|----------|---------------------------|-------------|
| Labelled examples | 19 | 19 |
| Judge calls | 19 × 5 = **95** | **19** |
| Turns per call | 1 (no tools, no loop) | 1 |
| Tokens per call | ~400 in, ~100 out | same |
| Total tokens | ~38k in, ~10k out | ~8k in, ~2k out |
| Cost (gpt-4o-mini) | **≈ $0.01** | ≈ $0.002 |

No agent runs: the answers are fixed text in `judge_validation/labels.json`.

## 6. CLI (`cli.py`)

One trial: the agent calls as above (usually 1–3), plus 5 judge calls only if you pass `--task` for a judge-graded task.

## Rate limits

`HTTP 429` means "too many requests". With OpenRouter there are two different 429s:

| Message | Whose limit | What helps |
|---------|-------------|------------|
| "Rate limit exceeded: free-models-per-min" / "…per-day" | **Your account's** quota for `:free` models | Wait (per-minute), wait until tomorrow (per-day), buy credits (raises the daily cap), or use a paid model |
| "**Provider returned error**" | The **upstream provider** hosting that free model is overloaded or rate-limited for everyone using it on OpenRouter | Retry later, try a different free model, or use a paid model. Your own quota may not be used up |

The free-model limits are about 20 requests per minute, and a per-day cap that is higher once you have bought
credits. That is OpenRouter's policy as last known; check their docs for current numbers.

**Retry amplification.** When a call is rate-limited, two layers retry:
1. Hermes retries each call itself, 3 attempts.
2. The platform's runner then retries the **whole trial**, up to `MAX_ATTEMPTS` = 4, so the calls that had already
   succeeded are repeated too.

Worst case: one stubborn rate limit can cost **~12 requests per call**, plus the repeated earlier calls. On free models:
- set `MAX_CONCURRENCY=1` (and consider `MAX_ATTEMPTS=2`) in `.env`;
- run fewer tasks or a smaller N while iterating;
- watch the experiment page: the circuit breaker stops a config whose first 3 trials all error.

**Request budget check before a free-model run.** If your daily cap is 1,000 requests, the ~660-request experiment
above fits only if you start with most of the day's quota left, and only if few retries happen.

## Ways to reduce calls and cost
- Deterministic graders wherever possible (0 LLM calls). Keep `llm_judge` for open-ended tasks only.
- Lower `judge_votes` to 3 *after* validation shows the judge is stable on your rubrics.
- Prompt caching: the harness system prompt is identical across calls, and providers that support prefix caching
  charge much less for it.
- Early stopping: run N=2 first, and add trials only for tasks where the configs disagree.
- Smaller `max_steps` for tasks that need few tool calls (it caps runaway loops).
- Reuse finished results: add a fixed config to an existing experiment instead of re-running everything.
