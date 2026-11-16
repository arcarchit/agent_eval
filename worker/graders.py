"""Graders. Each returns {"passed": bool, "score": float, "reasoning": str}.

Deterministic: exact, regex, file_check, unit_test.  Model-based: llm_judge (rubric).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import openai

from common import Trace, get_model, load_models


def normalize(s: str) -> str:
    s = (s or "").strip().strip("`\"'").strip().rstrip(".").lower()
    return re.sub(r"\s+", " ", s)


def grade(task: dict, trace: Trace, workspace: Path) -> dict:
    g = task["grader"]
    answer = trace.final_answer
    kind = g["type"]

    if kind == "exact":
        ok = normalize(answer) == normalize(str(g["expected"]))
        return _result(ok, f"expected {g['expected']!r}, got {answer.strip()[:200]!r}")

    if kind == "regex":
        ok = re.search(g["pattern"], answer.strip(), re.IGNORECASE | re.MULTILINE) is not None
        return _result(ok, f"pattern {g['pattern']!r} {'matched' if ok else 'did not match'}")

    if kind == "file_check":
        path = workspace / g["path"]
        if not path.exists():
            return _result(False, f"{g['path']} was not created")
        text = path.read_text()
        if "json_equals" in g:
            try:
                ok = json.loads(text) == g["json_equals"]
            except json.JSONDecodeError:
                return _result(False, f"{g['path']} is not valid JSON")
        elif "number" in g:
            try:
                ok = abs(float(text.strip()) - float(g["number"])) < 1e-6
            except ValueError:
                ok = False
        elif "contains" in g:
            ok = g["contains"].lower() in text.lower()
        else:
            ok = normalize(text) == normalize(str(g["equals"]))
        return _result(ok, f"{g['path']} content: {text.strip()[:200]!r}")

    if kind == "unit_test":
        # The test file is written AFTER the agent finished, so the agent never sees it.
        (workspace / "_eval_test.py").write_text(g["test_code"])
        r = subprocess.run([sys.executable, "_eval_test.py"], cwd=workspace,
                           capture_output=True, text=True, timeout=60)
        return _result(r.returncode == 0, "all tests passed" if r.returncode == 0 else r.stderr[-1500:])

    if kind == "llm_judge":
        return judge(task["prompt"], answer, g["rubric"])

    raise ValueError(f"unknown grader {kind}")


JUDGE_PROMPT = """You are grading an AI agent's answer against a rubric.
Task given to the agent:
<task>{task}</task>
Agent's final answer:
<answer>{answer}</answer>
Rubric (the answer passes only if ALL points hold):
<rubric>{rubric}</rubric>
Reply with JSON only: {{"reasoning": "<check each rubric point>", "verdict": "pass" or "fail"}}"""


def judge(task_prompt: str, answer: str, rubric: str, client=None) -> dict:
    """LLM-as-judge with a fixed rubric, temperature 0, reasoning before verdict."""
    model = get_model(load_models()["judge_model"])
    client = client or openai.OpenAI(base_url=model.base_url, api_key=model.api_key, max_retries=3)
    resp = client.chat.completions.create(
        model=model.provider_model, temperature=0, max_tokens=600,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(task=task_prompt, answer=answer, rubric=rubric)}])
    text = resp.choices[0].message.content or ""
    try:
        out = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except json.JSONDecodeError:
        out = {"verdict": "fail", "reasoning": f"judge returned unparseable output: {text[:300]}"}
    usage = resp.usage
    cost = model.cost(usage.prompt_tokens, usage.completion_tokens) if usage else 0.0
    res = _result(out.get("verdict") == "pass", out.get("reasoning", ""))
    res["judge_cost_usd"] = cost
    return res


def _result(passed: bool, reasoning: str) -> dict:
    return {"passed": bool(passed), "score": 1.0 if passed else 0.0, "reasoning": reasoning}
