"""Run ONE trial: one task, one config. Called by the API as a separate process.

stdin : {"task": {...}, "config": {...}}
stdout: one JSON line with the result (trace, grade, usage, failure category)
exit  : 0 = finished (passed or failed), 2 = retryable infra error (rate limit, 5xx),
        1 = error that retrying will not fix (out of credits, bad key, crash)

A fresh temp directory per trial gives isolation: the workspace and the harness home
(memory, skills) are thrown away afterwards.
"""
import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ProviderError, RetryableError, get_model  # noqa: E402
from failures import classify  # noqa: E402
from graders import grade  # noqa: E402
from harnesses import get_harness  # noqa: E402


def agent_view(task: dict) -> dict:
    """What the harness may see: prompt, memory_setup, setup files. Never the grader (expected answer,
    regex, hidden unit tests, judge rubric), so no adapter can leak it into the agent's context."""
    return {k: v for k, v in task.items() if k != "grader"}


def run(job: dict) -> dict:
    task, config = job["task"], job["config"]
    with tempfile.TemporaryDirectory(prefix="trial-") as tmp:
        workspace, home = Path(tmp, "workspace"), Path(tmp, "home")
        workspace.mkdir()
        home.mkdir()
        for name, content in task.get("setup_files", {}).items():
            (workspace / name).write_text(content)

        model = get_model(config["model"], config.get("model_spec"))
        start = time.time()
        trace = get_harness(config["harness"]).run(agent_view(task), config, model, workspace, home)
        latency_ms = int((time.time() - start) * 1000)
        result = grade(task, trace, workspace)

    return {
        "status": "ok",
        "passed": result["passed"],
        "score": result["score"],
        "grader": {"type": task["grader"]["type"], "reasoning": result["reasoning"]},
        "failure_category": classify(trace, result["passed"]),
        "trace": asdict(trace),
        "llm_calls": trace.llm_calls(),   # model calls across all sessions
        "turns": trace.turns(),           # user turns (sessions)
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "cost_usd": model.cost(trace.input_tokens, trace.output_tokens),
        "judge_cost_usd": result.get("judge_cost_usd", 0.0),
        "latency_ms": latency_ms,
    }


def main():
    job = json.load(sys.stdin)
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # harness libraries may print; keep stdout for our JSON only
    cwd = os.getcwd()
    try:
        out, code = run(job), 0
    except RetryableError as e:
        out, code = {"status": "retryable_error", "error": str(e)}, 2
    except ProviderError as e:  # e.g. out of credits: not the agent's fault, never graded
        out, code = {"status": "error", "error": str(e)}, 1
    except Exception as e:
        out, code = {"status": "error", "error": f"{e}\n{traceback.format_exc()[-2000:]}"}, 1
    finally:
        os.chdir(cwd)
    real_stdout.write(json.dumps(out) + "\n")
    real_stdout.flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
