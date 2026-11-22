"""Graders. Each returns {"passed": bool, "score": float, "reasoning": str}.

Deterministic: exact, regex, file_check, unit_test.  Model-based: llm_judge (rubric).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import openai

from common import Trace, call_model, get_model, load_models


def extract_answer(text: str) -> tuple[str, str]:
    """Tasks ask for {"answer": "..."}. Take the LAST JSON object with an "answer" key anywhere in
    the reply (also inside ```json fences or after some prose). If there is none, fall back to the
    whole reply, so a bare correct answer like "215" still passes.
    Returns (answer, how_it_was_found)."""
    decoder = json.JSONDecoder()
    found = None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "answer" in obj:
                found = str(obj["answer"])
    if found is not None:
        return found, "json answer field"
    return text or "", "no JSON found, used whole reply"


def normalize(s: str) -> str:
    s = (s or "").strip().strip("`\"'").strip().rstrip(".").lower()
    return re.sub(r"\s+", " ", s)


def grade(task: dict, trace: Trace, workspace: Path) -> dict:
    g, answer = task["grader"], trace.final_answer
    if g["type"] == "exact":
        return _exact(g, answer)
    if g["type"] == "regex":
        return _regex(g, answer)
    if g["type"] == "file_check":
        return _file_check(g, workspace)
    if g["type"] == "unit_test":
        return _unit_test(g, workspace)
    if g["type"] == "llm_judge":
        return judge(task["prompt"], answer, g["rubric"])
    raise ValueError(f"unknown grader {g['type']}")


def _exact(g: dict, answer: str) -> dict:
    value, how = extract_answer(answer)
    ok = normalize(value) == normalize(str(g["expected"]))
    return _result(ok, f"expected {g['expected']!r}, got {value.strip()[:200]!r} ({how})")


def _regex(g: dict, answer: str) -> dict:
    value, how = extract_answer(answer)
    ok = re.search(g["pattern"], value.strip(), re.IGNORECASE | re.MULTILINE) is not None
    return _result(ok, f"pattern {g['pattern']!r} {'matched' if ok else 'did not match'} {value.strip()[:200]!r} ({how})")


def _file_check(g: dict, workspace: Path) -> dict:
    """Checks a file the agent left behind: json_equals, number, contains, or equals (one of them)."""
    path = workspace / g["path"]
    if not path.exists():
        return _result(False, f"{g['path']} was not created")
    text = path.read_text()
    try:
        if "json_equals" in g:
            ok = json.loads(text) == g["json_equals"]
        elif "number" in g:
            ok = abs(float(text.strip()) - float(g["number"])) < 1e-6
        elif "contains" in g:
            ok = g["contains"].lower() in text.lower()
        else:
            ok = normalize(text) == normalize(str(g["equals"]))
    except ValueError:  # not valid JSON / not a number (JSONDecodeError is a ValueError)
        ok = False
    return _result(ok, f"{g['path']} content: {text.strip()[:200]!r}")


def _unit_test(g: dict, workspace: Path) -> dict:
    # The test file is written AFTER the agent finished, so the agent never sees it.
    (workspace / "_eval_test.py").write_text(g["test_code"])
    r = subprocess.run([sys.executable, "_eval_test.py"], cwd=workspace, capture_output=True, text=True, timeout=60)
    return _result(r.returncode == 0, "all tests passed" if r.returncode == 0 else r.stderr[-1500:])


JUDGE_PROMPT = """You are grading an AI agent's answer against a rubric.
Task given to the agent:
<task>{task}</task>
Agent's final answer:
<answer>{answer}</answer>
Rubric (the answer passes only if ALL points hold):
<rubric>{rubric}</rubric>
Judge only what the rubric says. Do not add stricter requirements of your own.
Reply with JSON only: {{"reasoning": "<check each rubric point>", "verdict": "pass" or "fail"}}"""


def judge(task_prompt: str, answer: str, rubric: str, client=None, votes: int | None = None) -> dict:
    """LLM-as-judge: fixed rubric, temperature 0, reasoning before verdict.

    Temperature 0 is NOT deterministic on hosted APIs: the same answer was observed to get
    3 passes and 4 fails (docs/JUDGE_VALIDATION.md). So we ask `votes` times (judge_votes in
    models.yaml) and take the majority. All votes are kept so instability stays visible.
    """
    cfg = load_models()
    model = get_model(cfg["judge_model"])
    votes = votes or cfg.get("judge_votes", 1)
    client = client or openai.OpenAI(base_url=model.base_url, api_key=model.api_key, max_retries=3)
    prompt = JUDGE_PROMPT.format(task=task_prompt, answer=answer, rubric=rubric)
    verdicts, reasons, cost = [], [], 0.0
    for _ in range(votes):
        resp = call_model(client, model, temperature=0, max_tokens=600, response_format={"type": "json_object"},
                          messages=[{"role": "user", "content": prompt}])
        text = resp.choices[0].message.content or ""
        try:
            out = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except json.JSONDecodeError:
            out = {"verdict": "fail", "reasoning": f"judge returned unparseable output: {text[:300]}"}
        verdicts.append(out.get("verdict") == "pass")
        reasons.append(out.get("reasoning", ""))
        if resp.usage:
            cost += model.cost(resp.usage.prompt_tokens, resp.usage.completion_tokens)

    passed = sum(verdicts) > votes / 2
    majority_reason = reasons[verdicts.index(passed)]  # show a reasoning that agrees with the result
    tally = f"[judge votes: {sum(verdicts)} pass / {votes - sum(verdicts)} fail] " if votes > 1 else ""
    res = _result(passed, tally + majority_reason)
    res["judge_cost_usd"] = cost
    res["judge_votes"] = verdicts
    return res


def _result(passed: bool, reasoning: str) -> dict:
    return {"passed": bool(passed), "score": 1.0 if passed else 0.0, "reasoning": reasoning}
