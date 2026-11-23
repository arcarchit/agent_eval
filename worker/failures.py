"""Rule-based failure categories, so a regression points to a cause.

Rules are checked in order; the first one that matches wins.
"""
import json

from common import Trace


def classify(trace: Trace, passed: bool, timed_out: bool = False) -> str | None:
    if passed:
        return None
    if timed_out:
        return "timeout"
    if trace.stop_reason == "error":
        return "provider_error"           # safety net: should already have ended as status "error"
    if trace.stop_reason == "step_limit":
        return "step_exhaustion"          # ran out of steps, often a loop
    if trace.stop_reason == "loop_guard":
        return "loop"                     # the harness itself stopped a repeating tool loop
    if trace.stop_reason == "truncated":
        return "truncated"                # reply hit max_tokens: a config limit, not (only) the model
    calls = [e for e in trace.events if e["type"] == "tool_call"]
    results = [e for e in trace.events if e["type"] == "tool_result"]
    if any(trace.available_tools and c["name"] not in trace.available_tools for c in calls):
        return "hallucinated_tool"        # called a tool that does not exist
    if any(not _valid_json(c.get("arguments")) for c in calls):
        return "bad_tool_args"            # arguments were not valid JSON
    if results and sum(r.get("error", False) for r in results) >= max(2, len(results) // 2):
        return "tool_errors"              # most tool calls failed
    if _looped(calls):
        return "loop"                     # same call repeated 3+ times
    if not trace.final_answer.strip():
        return "gave_up"                  # ended with no answer
    return "wrong_answer"


def _valid_json(s) -> bool:
    if not isinstance(s, str):
        return True
    try:
        json.loads(s or "{}")
        return True
    except json.JSONDecodeError:
        return False


def _looped(calls) -> bool:
    keys = [(c["name"], c.get("arguments")) for c in calls]
    return any(keys.count(k) >= 3 for k in set(keys))
