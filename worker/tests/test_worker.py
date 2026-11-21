"""Worker tests. No network: the model is a scripted fake client."""
import json
import sys
from pathlib import Path

import pytest
import yaml
from openai.types.chat import ChatCompletion

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import ROOT, Model, Trace  # noqa: E402
from failures import classify  # noqa: E402
from graders import grade, normalize  # noqa: E402
from harnesses.baseline import BaselineHarness  # noqa: E402
from judge_validation import cohens_kappa  # noqa: E402

MODEL = Model("fake", "fake/model", "http://x", "k", 1.0, 2.0)


# ---------- fake OpenAI client ----------
def reply(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(tool_calls)]
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot
        return self.replies.pop(0)


class FakeBaseline(BaselineHarness):
    def __init__(self, replies):
        self.client = FakeClient(replies)

    def make_client(self, model):
        return self.client


def dirs(tmp_path):
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir(), home.mkdir()
    return ws, home


# ---------- graders ----------
def test_normalize():
    assert normalize("  `Thursday.` ") == "thursday"


def test_exact_and_regex():
    t = Trace(final_answer="215")
    assert grade({"grader": {"type": "exact", "expected": "215"}}, t, Path("."))["passed"]
    t = Trace(final_answer="$72.00")
    assert grade({"grader": {"type": "regex", "pattern": r"^\$?72(\.00?)?$"}}, t, Path("."))["passed"]


def test_file_check_and_unit_test(tmp_path):
    (tmp_path / "out.json").write_text('{"a": 1}')
    assert grade({"grader": {"type": "file_check", "path": "out.json", "json_equals": {"a": 1}}},
                 Trace(), tmp_path)["passed"]
    assert not grade({"grader": {"type": "file_check", "path": "missing.txt", "equals": "x"}},
                     Trace(), tmp_path)["passed"]
    (tmp_path / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    g = {"type": "unit_test", "test_code": "from solution import add\nassert add(2, 2) == 4\n"}
    assert grade({"grader": g}, Trace(), tmp_path)["passed"]
    g["test_code"] = "from solution import add\nassert add(2, 2) == 5\n"
    assert not grade({"grader": g}, Trace(), tmp_path)["passed"]


def test_all_tasks_are_valid():
    tasks = [yaml.safe_load(p.read_text()) for p in (ROOT / "tasks").glob("*.yaml")]
    assert len(tasks) >= 20
    assert len({t["id"] for t in tasks}) == len(tasks)
    for t in tasks:
        assert t["grader"]["type"] in {"exact", "regex", "file_check", "unit_test", "llm_judge"}


# ---------- baseline harness ----------
def test_baseline_tool_loop(tmp_path):
    ws, home = dirs(tmp_path)
    h = FakeBaseline([reply(tool_calls=[("write_file", {"path": "total.txt", "content": "775"})]),
                      reply("done")])
    trace = h.run({"prompt": "write it"}, {"tools": ["file"]}, MODEL, ws, home)
    assert (ws / "total.txt").read_text() == "775"
    assert trace.final_answer == "done"
    assert trace.input_tokens == 20 and trace.steps == 2
    assert [e["type"] for e in trace.events] == [
        "session_start", "user", "assistant", "tool_call", "tool_result", "assistant", "session_end"]


def test_baseline_blocks_path_escape(tmp_path):
    ws, home = dirs(tmp_path)
    h = FakeBaseline([reply(tool_calls=[("read_file", {"path": "../../etc/passwd"})]), reply("x")])
    trace = h.run({"prompt": "p"}, {"tools": ["file"]}, MODEL, ws, home)
    result = [e for e in trace.events if e["type"] == "tool_result"][0]
    assert result["error"] and "escapes" in result["output"]


def test_memory_carries_across_sessions_only_when_enabled(tmp_path):
    ws, home = dirs(tmp_path)
    task = {"memory_setup": ["remember X=1"], "prompt": "what is X?"}
    h = FakeBaseline([reply(tool_calls=[("memory_write", {"note": "X=1"})]), reply("saved"),
                      reply(tool_calls=[("memory_read", {})]), reply("1")])
    trace = h.run(task, {"memory": True}, MODEL, ws, home)
    reads = [e for e in trace.events if e["type"] == "memory_read"]
    assert reads[0]["content"] == "X=1" and trace.final_answer == "1"
    # second session starts with a fresh message history (only system + new user message)
    assert len(h.client.requests[2]["messages"]) == 2

    h = FakeBaseline([reply("ok"), reply("I don't know")])
    tools = h.run(task, {"memory": False}, MODEL, ws, tmp_path / "home2")
    assert "memory_read" not in str(h.client.requests[0].get("tools"))
    assert tools.final_answer == "I don't know"


def test_step_limit(tmp_path):
    ws, home = dirs(tmp_path)
    h = FakeBaseline([reply(tool_calls=[("list_files", {})])] * 3)
    trace = h.run({"prompt": "p"}, {"tools": ["file"], "max_steps": 3}, MODEL, ws, home)
    assert trace.stop_reason == "step_limit"
    assert classify(trace, passed=False) == "step_exhaustion"


# ---------- failure categories ----------
def test_failure_categories():
    t = Trace(available_tools=["read_file"], final_answer="x")
    t.add("tool_call", name="delete_everything", arguments="{}")
    assert classify(t, False) == "hallucinated_tool"
    t = Trace(final_answer="")
    assert classify(t, False) == "gave_up"
    assert classify(Trace(final_answer="42"), False) == "wrong_answer"
    assert classify(Trace(), True) is None


def test_cohens_kappa():
    assert cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert cohens_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0


def test_judge_uses_rubric_and_parses_verdict():
    from graders import judge
    client = FakeClient([reply('{"reasoning": "all good", "verdict": "pass"}')])
    res = judge("task", "answer", "rubric text", client=client, votes=1)
    assert res["passed"] and "rubric text" in client.requests[0]["messages"][0]["content"]
    assert client.requests[0]["temperature"] == 0


def test_judge_majority_vote_keeps_all_votes():
    from graders import judge
    p, f = '{"reasoning": "ok", "verdict": "pass"}', '{"reasoning": "jargon", "verdict": "fail"}'
    res = judge("t", "a", "r", client=FakeClient([reply(f), reply(p), reply(p)]), votes=3)
    assert res["passed"] and res["judge_votes"] == [False, True, True]
    assert res["reasoning"].startswith("[judge votes: 2 pass / 1 fail] ok")
    res = judge("t", "a", "r", client=FakeClient([reply(f), reply(f), reply(p)]), votes=3)
    assert not res["passed"] and "jargon" in res["reasoning"]


# ---------- JSON answer extraction ----------
def test_extract_answer():
    from graders import extract_answer
    assert extract_answer('{"answer": "215"}') == ("215", "json answer field")
    assert extract_answer('The trip takes:\n```json\n{"answer": 215}\n```')[0] == "215"
    assert extract_answer('first {"answer": "1"} then {"answer": "2"}')[0] == "2"   # last one wins
    assert extract_answer("215") == ("215", "no JSON found, used whole reply")
    t = Trace(final_answer='Sure! {"answer": "ACME-25-GLO-0007"}')
    assert grade({"grader": {"type": "exact", "expected": "ACME-25-GLO-0007"}}, t, Path("."))["passed"]
    t = Trace(final_answer="The trip is 215 minutes.")  # ignored the format: still a fail
    assert not grade({"grader": {"type": "exact", "expected": "215"}}, t, Path("."))["passed"]


# ---------- provider errors are never graded ----------
def test_baseline_out_of_credits_is_provider_error(tmp_path):
    import types
    import openai
    from common import ProviderError

    class Broke(FakeClient):
        def create(self, **kw):
            resp = types.SimpleNamespace(status_code=402, headers={}, request=None)
            raise openai.APIStatusError("Insufficient credits", response=resp, body=None)

    h = FakeBaseline([])
    h.client = Broke([])
    ws, home = dirs(tmp_path)
    with pytest.raises(ProviderError, match="402"):
        h.run({"prompt": "p"}, {}, MODEL, ws, home)


def fake_hermes(monkeypatch, result: dict):
    """Replace Hermes' AIAgent with a stub that returns `result` from run_conversation."""
    import types

    class AIAgent:
        def __init__(self, **kw):
            self.valid_tool_names = {"read_file"}

        def run_conversation(self, message):
            return result

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=AIAgent))
    monkeypatch.chdir(Path.cwd())  # the adapter chdirs into the workspace; restore afterwards
    from harnesses.hermes import HermesHarness
    return HermesHarness()


def test_hermes_402_is_provider_error_not_wrong_answer(tmp_path, monkeypatch):
    from common import ProviderError, RetryableError
    ws, home = dirs(tmp_path)
    h = fake_hermes(monkeypatch, {"failed": True, "failure_retryable": False,
                                  "error": "Billing or credits exhausted: HTTP 402", "final_response": "Billing..."})
    with pytest.raises(ProviderError, match="402"):
        h.run({"prompt": "p"}, {}, MODEL, ws, home)
    h = fake_hermes(monkeypatch, {"failed": True, "failure_retryable": True, "error": "429"})
    with pytest.raises(RetryableError):
        h.run({"prompt": "p"}, {}, MODEL, ws, tmp_path / "h2")


def test_hermes_loop_guard_and_session_end(tmp_path, monkeypatch):
    ws, home = dirs(tmp_path)
    h = fake_hermes(monkeypatch, {"messages": [], "final_response": "", "api_calls": 4,
                                  "turn_exit_reason": "guardrail_halt"})
    trace = h.run({"prompt": "p"}, {}, MODEL, ws, home)
    assert trace.stop_reason == "loop_guard" and classify(trace, False) == "loop"
    assert trace.events[-1] == {**trace.events[-1], "type": "session_end", "exit_reason": "guardrail_halt"}


def test_hermes_tool_error_detection():
    from harnesses.hermes import is_tool_error
    assert is_tool_error('{"success": false, "message": "x"}')
    assert is_tool_error('{"error": "file not found"}')
    assert is_tool_error("Refusing to write: content looks double-escaped")
    assert not is_tool_error('{"success": true, "content": "Error handling is described here"}')
