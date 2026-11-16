"""Adapter for Hermes Agent (https://github.com/NousResearch/hermes-agent).

Hermes keeps all its state (memory, skills, sessions) in HERMES_HOME. We point HERMES_HOME at the
trial's private temp dir, so every trial starts clean. Memory carries across sessions inside one
trial only when the config turns memory on.
"""
import json
import os
import shutil
from pathlib import Path

from common import ROOT, Model, RetryableError, Trace
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
        from run_agent import AIAgent  # import after HERMES_HOME is set

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

            if result.get("failed") and result.get("failure_retryable"):
                raise RetryableError(result.get("error") or "hermes provider error")
            self._add_messages(trace, result.get("messages", []))
            trace.input_tokens += result.get("input_tokens") or 0
            trace.output_tokens += result.get("output_tokens") or 0
            trace.steps = result.get("api_calls") or 0
            trace.final_answer = result.get("final_response") or ""
            exit_reason = result.get("turn_exit_reason") or ""
            trace.stop_reason = ("step_limit" if exit_reason == "budget_exhausted"
                                 else "error" if result.get("failed") else "completed")
        return trace

    @staticmethod
    def _add_messages(trace: Trace, messages: list):
        """Convert Hermes' OpenAI-style message list into our trace events."""
        for m in messages:
            if m["role"] == "user":
                trace.add("user", content=m.get("content"))
            elif m["role"] == "assistant":
                calls = [c["function"] for c in m.get("tool_calls") or []]
                trace.add("assistant", content=m.get("content") or "", tool_calls=calls)
                for c in calls:
                    trace.add("tool_call", name=c["name"], arguments=c["arguments"])
                    args = _json(c["arguments"])
                    if c["name"] == "memory" and args.get("action") in ("add", "replace", "remove"):
                        trace.add("memory_write", content=args.get("content") or args.get("old_text"))
                    if c["name"] == "skill_view":
                        trace.add("skill_used", name=args.get("name"))
            elif m["role"] == "tool":
                content = str(m.get("content", ""))
                trace.add("tool_result", name=m.get("name"), output=content[:4000],
                          error='"success": false' in content or content.startswith("Error"))


def _json(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except json.JSONDecodeError:
        return {}
