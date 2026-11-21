"""Adapter for Hermes Agent (https://github.com/NousResearch/hermes-agent).

Hermes keeps all its state (memory, skills, sessions) in HERMES_HOME. We point HERMES_HOME at the
trial's private temp dir, so every trial starts clean. Memory carries across sessions inside one
trial only when the config turns memory on.
"""
import json
import os
import shutil
from pathlib import Path

from common import ROOT, Model, ProviderError, RetryableError, Trace
from harnesses.base import Harness, sessions

SKILLS_DIR = ROOT / "skills"


class HermesHarness(Harness):
    name = "hermes"

    def run(self, task, config, model: Model, workspace: Path, home: Path) -> Trace:
        hermes_home = home / "hermes"
        for skill in config.get("skills", []):  # install only the skills this config enables
            shutil.copytree(SKILLS_DIR / skill, hermes_home / "skills" / skill)
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["TERMINAL_CWD"] = str(workspace)
        os.chdir(workspace)
        # Hermes is installed in its own virtualenv (/opt/hermes/.venv, on PYTHONPATH in Docker), so an
        # editor using another Python interpreter reports this import as unresolved. It works at runtime.
        from run_agent import AIAgent  # pyright: ignore[reportMissingImports]  (import after HERMES_HOME is set)

        toolsets = list(config.get("tools", []))
        if config.get("memory"):
            toolsets.append("memory")
        if config.get("skills"):
            toolsets.append("skills")

        trace = Trace()
        for i, message in enumerate(sessions(task)):
            trace.add("session_start", index=i)
            memory_file = hermes_home / "memories" / "MEMORY.md"
            if config.get("memory") and memory_file.exists():
                # Hermes injects MEMORY.md into the system prompt at session start: that is the "read".
                trace.add("memory_read", content=memory_file.read_text())
            agent = AIAgent(
                base_url=model.base_url, api_key=model.api_key, model=model.provider_model,
                max_iterations=config.get("max_steps", 10), max_tokens=config.get("max_tokens", 1024),
                request_overrides={"temperature": config.get("temperature", 0.0)},
                enabled_toolsets=toolsets, skip_memory=not config.get("memory"),
                skip_context_files=True, quiet_mode=True, platform="batch", cwd=str(workspace))
            trace.available_tools = sorted(agent.valid_tool_names) if hasattr(agent, "valid_tool_names") else []
            result = agent.run_conversation(message)

            # A failed turn means the model API call itself failed (rate limit, HTTP 402 out of
            # credits, 5xx, auth). That is infrastructure, not agent behaviour: never grade it.
            # Retryable ones go back to the runner for backoff; the rest end the trial as "error".
            if result.get("failed"):
                error = f"provider error in session {i}: {result.get('error') or result.get('failure_reason')}"
                if result.get("failure_retryable"):
                    raise RetryableError(error)
                raise ProviderError(error)
            self._add_messages(trace, result.get("messages", []))
            trace.input_tokens += result.get("input_tokens") or 0
            trace.output_tokens += result.get("output_tokens") or 0
            trace.steps = result.get("api_calls") or 0
            trace.final_answer = result.get("final_response") or ""
            exit_reason = result.get("turn_exit_reason") or ""
            trace.stop_reason = {"budget_exhausted": "step_limit",   # hit max_steps
                                 "guardrail_halt": "loop_guard",     # Hermes stopped a repeating tool loop
                                 }.get(exit_reason, "completed")
            # Recorded for every session, so a problem in an earlier (memory_setup) session stays visible.
            trace.add("session_end", index=i, stop_reason=trace.stop_reason, exit_reason=exit_reason)
        return trace

    @staticmethod
    def _add_messages(trace: Trace, messages: list):
        """Convert Hermes' OpenAI-style message list into our trace events. Each Hermes message carries its own
        "timestamp", which we keep, so per-step durations in the UI are real."""
        for m in messages:
            ts = m.get("timestamp")
            if m["role"] == "user":
                trace.add("user", ts=ts, content=m.get("content"))
            elif m["role"] == "assistant":
                calls = [c["function"] for c in m.get("tool_calls") or []]
                trace.add("assistant", ts=ts, content=m.get("content") or "", tool_calls=calls)
                for c in calls:
                    trace.add("tool_call", ts=ts, name=c["name"], arguments=c["arguments"])
                    args = _json(c["arguments"])
                    if c["name"] == "memory" and args.get("action") in ("add", "replace", "remove"):
                        trace.add("memory_write", ts=ts, content=args.get("content") or args.get("old_text"))
                    if c["name"] == "skill_view":
                        trace.add("skill_used", ts=ts, name=args.get("name"))
            elif m["role"] == "tool":
                content = str(m.get("content", ""))
                trace.add("tool_result", ts=ts, name=m.get("name"), output=content[:4000], error=is_tool_error(content))


def is_tool_error(content: str) -> bool:
    """Hermes tools return JSON like {"success": false, ...} or {"error": "..."}, or plain text."""
    data = _json(content)
    if isinstance(data, dict) and (data.get("success") is False or data.get("error")):
        return True
    return content.lstrip().lower().startswith(("error", "refusing", "failed"))


def _json(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except json.JSONDecodeError:
        return {}
