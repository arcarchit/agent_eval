# Guide: running evaluations and extending the platform

1. [Quick start](#1-quick-start)
2. [Your first comparison](#2-your-first-comparison)
3. [Add a task](#3-add-a-task)
4. [Add a model](#4-add-a-model)
5. [Add a harness](#5-add-a-harness)
6. [Add a skill](#6-add-a-skill)
7. [Validate the LLM judge](#7-validate-the-llm-judge)
8. [Troubleshooting](#8-troubleshooting)

API details are in [API.md](API.md). What the platform can and cannot do is in [FEATURES.md](FEATURES.md).
Expected calls, tokens and cost are in [USAGE_AND_COST.md](USAGE_AND_COST.md).

---

## 1. Quick start

**With Docker**
```bash
cp .env.example .env              # put OPENROUTER_API_KEY in it
docker compose up --build         # http://localhost:3000
```

**Without Docker** (macOS: `brew install node@22 uv`)
```bash
cp .env.example .env
./scripts/run-local.sh            # first run installs Hermes into ~/hermes-agent
```

**Try one prompt** before running a whole experiment. This catches a wrong model id or key in seconds:
```bash
./scripts/run-local.sh cli --model gpt-4o-mini "What is 17% of 2340?"
docker compose run --rm evals /opt/hermes/.venv/bin/python /app/worker/cli.py --model gpt-4o-mini "What is 17% of 2340?"
```

**What needs a restart?**

| You change | Do |
|------------|----|
| `models.yaml`, `tasks/`, `skills/` | nothing; refresh the page |
| `.env` | `docker compose up -d` (`restart` does not re-read `.env`) |
| code or `Dockerfile` | `docker compose up --build` |

## 2. Your first comparison

Compare **one change at a time**, otherwise you cannot tell which change caused the difference.

1. **Configs page.** Create two configs that differ in one setting, for example `hermes-skills` (skills
   `acme-billing`, `acme-style`) and `hermes-noskills` (no skills). Keep the model, tools, temperature (0) and budgets identical.
2. **Experiments page.** Tick both configs and set N = 5. Pick tasks, or leave them all, then click **Run**. The
   button shows how many trials will run. Check [USAGE_AND_COST.md](USAGE_AND_COST.md) for the expected calls and cost.
3. **Read the result** on the experiment page:
   - The **verdict** says `A better` / `B better` only if the 95% confidence interval excludes 0. Otherwise the
     difference may be noise.
   - **Per-task deltas** show where a difference comes from.
   - **Cost, latency and tokens** sit next to quality, so you can see the trade-offs.
   - **Errors** (infrastructure) and **stopped** trials are excluded from pass rates and shown as counts.
4. **Drill down.** Click a cell in the per-task table, then a trial, to see the whole trajectory and the grader's reasoning.
5. **Stop or fix.** Use **Stop this config** if a config is misconfigured; it also stops by itself after 3 initial
   errors. To fix it: correct `models.yaml`, re-save the config (it becomes `@v2`), then **Add a config to this
   experiment**. The finished trials of the other config are reused.
6. **Export** the results as JSON or CSV with the links at the top.

## 3. Add a task

Create `tasks/<id>.yaml`. It is picked up immediately.

```yaml
id: refund-01
category: support
needs: skill:acme-billing          # optional: documents what should make configs differ
setup_files:                       # optional: files placed in the agent's workspace
  orders.csv: |
    id,amount
    1,40
    2,60
memory_setup:                      # optional: earlier sessions, run before the prompt
  - "Remember: refunds over $50 need manager approval."
prompt: >
  Which order ids in orders.csv need manager approval for a refund?
  Reply with JSON only, like {"answer": "<comma-separated ids>"}.
grader: { type: exact, expected: "2" }
```

**Graders**

| Type | Fields | Checks |
|------|--------|--------|
| `exact` | `expected` | the `answer` field of the last `{"answer": ...}` JSON in the reply (or the whole reply), trimmed and lower-cased |
| `regex` | `pattern` | same answer extraction, then a case-insensitive regex search |
| `file_check` | `path` + one of `equals`, `number`, `json_equals`, `contains` | a file the agent left in the workspace |
| `unit_test` | `test_code` | Python asserts run against the workspace **after** the agent finishes; the agent never sees them |
| `llm_judge` | `rubric` | a model checks every rubric point (5 votes, majority wins) |

**Writing tasks that measure what you want**
- Prefer deterministic graders. Use `llm_judge` only for open-ended writing, and then validate it (section 7).
- Ask for `{"answer": ...}` in the prompt, so formatting does not decide the score.
- Make sure exactly one answer is correct. Re-read the prompt as a pedantic agent would; ambiguous tasks measure noise.
- In a judge rubric, spell out what passes and what fails. "No jargon" was too vague in practice; "technical
  terms such as *Rayleigh* or *wavelength* used without a plain explanation" works.
- To test a skill or memory, the task must be impossible without it (made-up rules, facts from an earlier session).
- Check that the task parses: `python -m pytest worker/tests -k all_tasks_are_valid`.

## 4. Add a model

Any OpenAI-compatible chat API works. Add an entry to `models.yaml`, with no code and no restart:

```yaml
models:
  my-model:                                   # the name used in configs
    provider_model: mistralai/mistral-small   # the provider's model id
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY           # name of the env var holding the key (set it in .env)
    price_per_mtok: { input: 0.10, output: 0.30 }   # USD per 1M tokens, used for cost reporting
```

| Provider | `base_url` | Key variable |
|----------|-----------|--------------|
| OpenRouter (many providers) | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| xAI (Grok) | `https://api.x.ai/v1` | `XAI_API_KEY` |
| Groq | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| Ollama (local, free) | `http://host.docker.internal:11434/v1` (Docker) or `http://localhost:11434/v1` | any value |

Then:
1. If the key variable is new, add it to `.env` and run `docker compose up -d`.
2. Test it: `cli.py --model my-model "Say hi"`.
3. Create a config that uses it.

**Fair model comparisons: pin who serves the model.** On OpenRouter one model is served by several companies,
some with quantized (lower-precision) weights, and requests can move between them. Add the same
`provider_routing` to both models (a YAML anchor guarantees it is identical):
```yaml
  llama-3.1-8b:
    ...
    provider_routing: &full_precision { quantizations: [bf16, fp16], require_parameters: true, allow_fallbacks: false }
  ministral-8b:
    ...
    provider_routing: *full_precision
```
It is sent as OpenRouter's `provider` field by every harness and by the judge (one helper, `Model.request_params`).
If OpenRouter answers "no endpoints found", loosen the rule for **both** models, never for one.
The comparison panel lists which config settings differ and warns when more than one does.

Notes:
- The model must support **tool calling** for agent tasks. On OpenRouter, check that the model lists "tools".
- Free models (`:free`) are rate-limited. Set `MAX_CONCURRENCY=1`.
- To change the LLM judge, set `judge_model:` to one of these entries, then re-run the judge validation (section 7).
- Existing configs keep the model details they were saved with. Re-save a config to pick up an edited entry.

## 5. Add a harness

A harness adapter turns "run this task with this model" into a **trace**. The platform core (runner, graders,
statistics, UI) never changes. Below is a complete, tested example: a harness that calls the model once, with no tools.

**Step 1: write the adapter**, `worker/harnesses/direct.py`:
```python
"""Direct harness: one model call, no tools, no loop. The simplest possible agent."""
from pathlib import Path

import openai

from common import Model, Trace, call_model
from harnesses.base import Harness, sessions


class DirectHarness(Harness):
    name = "direct"

    def run(self, task: dict, config: dict, model: Model, workspace: Path, home: Path) -> Trace:
        trace = Trace()                                   # 1. start an empty trace
        client = self.make_client(model)
        for i, message in enumerate(sessions(task)):      # 2. one session per memory_setup message + the prompt
            trace.add("session_start", index=i)
            trace.add("user", content=message)
            # 3. call the model. call_model sends the shared fields (temperature, provider routing) and turns
            #    provider failures into RetryableError / ProviderError, so they are never graded.
            resp = call_model(client, model, config.get("temperature", 0), max_tokens=config.get("max_tokens", 1024),
                              messages=[{"role": "user", "content": message}])
            answer = resp.choices[0].message.content or ""
            trace.add("assistant", content=answer, tool_calls=[])   # 4. record what happened
            if resp.usage:                                # 5. tokens -> cost
                trace.input_tokens += resp.usage.prompt_tokens
                trace.output_tokens += resp.usage.completion_tokens
            trace.final_answer = answer                   # 6. what the grader sees
            trace.add("session_end", index=i, stop_reason="completed")
        return trace

    def make_client(self, model: Model):                 # overridden in tests with a fake
        return openai.OpenAI(base_url=model.base_url, api_key=model.api_key, max_retries=2)
```

**The contract your adapter must keep**

| Rule | Why |
|------|-----|
| Use only `workspace` (agent files) and `home` (harness state). Both are fresh temp folders per trial. | Isolation: nothing leaks between trials. |
| Run each `memory_setup` message as its own session, then the prompt in a **new** session (`sessions(task)`). Carry state across sessions only through memory, and only if `config["memory"]`. | Memory experiments stay meaningful. |
| Call models through `call_model()`, or, if the framework makes its own calls, pass it `model.request_params(...)` and raise `RetryableError` (rate limits, 5xx) / `ProviderError` (402, 401, unknown model) on failure. Never return an error as an answer. | Every harness sends the same temperature and provider routing (a fair comparison), and infrastructure failures are never graded as agent failures. |
| Record events: `user`, `assistant`, `tool_call`, `tool_result` (`error=True` on failure), `memory_read`, `memory_write`, `skill_used`, `session_start`, `session_end`. | The trajectory view and the failure categories are built from them. |
| Set `final_answer`, `input_tokens`, `output_tokens`, `stop_reason` (`completed`, `step_limit`, `loop_guard`), and `available_tools`. | Grading, cost, and the `step_exhaustion` / `hallucinated_tool` categories. |
| Respect `max_steps`, `max_tokens`, `temperature`, `tools`, `skills`. | Configs must mean the same thing for every harness. |
| You will never see the grader: the task you receive has no `grader` key. | Keeps expected answers out of the agent's context. |

**Step 2: register it.** In `worker/harnesses/__init__.py`:
```python
    if name == "direct":
        from harnesses.direct import DirectHarness
        return DirectHarness()
```
Then add `"direct"` to `HARNESSES` in `api/src/catalog.ts`, so the UI and API accept it. Rebuild with
`docker compose up --build`.

**Step 3: test it without a real model**, reusing the platform's fake client (`worker/tests/test_my_harness.py`):
```python
from test_worker import MODEL, FakeClient, reply
from harnesses.direct import DirectHarness

def test_direct_harness(tmp_path):
    h = DirectHarness()
    h.make_client = lambda model: FakeClient([reply('{"answer": "215"}')])
    trace = h.run({"prompt": "How many minutes?"}, {}, MODEL, tmp_path, tmp_path)
    assert trace.final_answer == '{"answer": "215"}' and trace.input_tokens == 10
    assert [e["type"] for e in trace.events] == ["session_start", "user", "assistant", "session_end"]
```
Run `python -m pytest worker/tests`, then one real prompt with `cli.py --harness direct "..."`.

**Wrapping an existing framework** (LangGraph, OpenAI Agents SDK, smolagents, …) follows the same pattern:
1. Build the framework's agent from `config`, pointing its model client at `model.base_url` and `model.api_key`.
2. Point its working directory or state at `workspace` / `home`.
3. Run it.
4. Convert its message history into trace events.

The Hermes adapter (`worker/harnesses/hermes.py`) is a real example: it points `HERMES_HOME` at the trial's `home`
folder, enables toolsets from the config, and translates Hermes' message list.

## 6. Add a skill

Create `skills/<name>/SKILL.md`:
```markdown
---
name: acme-refunds
description: ACME refund policy - approval thresholds and timelines
---
# ACME refund rules
...
```
It appears in the config form immediately. Hermes gets a private copy of each enabled skill per trial. The
baseline harness lists enabled skills in its system prompt and loads them with a `read_skill` tool. Edits Hermes
makes to a skill during a trial are thrown away.

## 7. Validate the LLM judge

Before trusting a task graded by `llm_judge`, check the judge against human labels:

1. Add examples to `judge_validation/labels.json`: `task_id`, an `answer`, your `human_label` (`pass`/`fail`), and a
   `note`. Include subtle failures, not just obvious ones.
2. Run `python worker/judge_validation.py` (5 votes per example), and `--votes 1` to see raw single-call behaviour.
3. Read `judge_validation/report.json`:
   - **accuracy**
   - **Cohen's kappa**: agreement corrected for chance
   - **false_pass** / **false_fail**
   - **unstable_items**: the same input got different verdicts
4. Accept the judge if kappa ≥ 0.8, there is at most 1 false pass, and there are 0 unstable items. Otherwise tighten
   the rubric and run it again.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `HTTP 429: Provider returned error` | Rate limit: yours (per minute or per day), or the free model's shared upstream capacity | `MAX_CONCURRENCY=1`, wait, use a paid model, or add credits. See [USAGE_AND_COST.md](USAGE_AND_COST.md#rate-limits) |
| `HTTP 402` | Out of credits | Top up. These trials end as `error`, ungraded |
| `HTTP 400/404` "not a valid model ID" | Wrong `provider_model` | Fix `models.yaml`, re-save the config (new version), and add it to the experiment |
| A config stops after a few trials, with the trials "stopped" | Circuit breaker: its first 3 trials all errored | Open one errored trial to see the message |
| `service "evals" is not running` | `docker compose exec` needs a running container | `docker compose up -d`, or use `docker compose run --rm evals ...` |
| Every answer fails exact match | The model ignores the JSON format | Open a trial: the grader reasoning shows `(no JSON found, used whole reply)` |
| Editor: `Import "run_agent" could not be resolved` | Hermes lives in its own virtualenv | Harmless. Select `~/hermes-agent/.venv` as the interpreter to clear it |
