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

# Hermes' reason for ending a turn -> our stop_reason (anything else counts as "completed").
# Matched by prefix: Hermes appends details, e.g. "max_iterations_reached(10/10)".
STOP_REASONS = {"max_iterations_reached": "step_limit",  # hit max_steps
                "budget_exhausted": "step_limit",        # older Hermes name for the same thing
                "guardrail_halt": "loop_guard"}          # Hermes stopped a repeating tool loop
# Hermes answers a reply cut off by max_tokens by asking the model to continue (a few times), so the turn
# can still succeed. The nudge is a user message starting with this text; we surface it as a warning.
LENGTH_NUDGE = "[System: Your previous response was truncated by the output length limit"


def stop_reason_for(exit_reason: str) -> str:
    return next((v for k, v in STOP_REASONS.items() if exit_reason.startswith(k)), "completed")


class HermesHarness(Harness):
    name = "hermes"

    def run(self, task, config, model: Model, workspace: Path, home: Path) -> Trace:
        hermes_home = self._prepare_home(config, home, workspace)
        # Hermes lives in its own virtualenv (on PYTHONPATH in Docker); an editor using another Python reports
        # this import as unresolved. Imported here, after HERMES_HOME is set, because Hermes reads it at import.
        from run_agent import AIAgent  # pyright: ignore[reportMissingImports]

        trace = Trace()
        for i, message in enumerate(sessions(task)):
            trace.add("session_start", index=i)
            start_ts = trace.events[-1]["ts"]
            memory_file = hermes_home / "memories" / "MEMORY.md"
            if config.get("memory") and memory_file.exists():
                # Hermes injects MEMORY.md into the system prompt at session start: that is the "read".
                trace.add("memory_read", content=memory_file.read_text())
            agent = AIAgent(
                base_url=model.base_url, api_key=model.api_key, model=model.provider_model,
                max_iterations=config.get("max_steps", 10), max_tokens=config.get("max_tokens", 1024),
                request_overrides=model.request_params(config.get("temperature", 0.0)),  # same fields as baseline
                enabled_toolsets=self._toolsets(config), skip_memory=not config.get("memory"),
                skip_context_files=True, quiet_mode=True, platform="batch", cwd=str(workspace))
            trace.available_tools = sorted(getattr(agent, "valid_tool_names", []))
            result = agent.run_conversation(message)
            self._raise_if_provider_failed(result, session=i)

            # Hermes builds its system prompt inside run_conversation (identity, guidance, skills index,
            # memory, environment). Recorded with session_start's timestamp so it shows no duration.
            trace.add("system", ts=start_ts, content=getattr(agent, "_cached_system_prompt", None) or "(not available)")
            self._add_messages(trace, result.get("messages", []))
            trace.input_tokens += result.get("input_tokens") or 0
            trace.output_tokens += result.get("output_tokens") or 0
            trace.final_answer = result.get("final_response") or ""
            exit_reason = result.get("turn_exit_reason") or ""
            trace.stop_reason = stop_reason_for(exit_reason)
            # A turn can end early without failing (failed=False); Hermes then stamps failure_reason, e.g.
            # "truncated" = reply hit max_tokens. final_response is then Hermes' explanation, not the model's.
            if result.get("failure_reason"):
                trace.add("warning", name=result["failure_reason"], content=result.get("final_response") or "")
                if result["failure_reason"] == "truncated":
                    trace.stop_reason = "truncated"
            # Recorded for every session, so a problem in an earlier (memory_setup) session stays visible.
            trace.add("session_end", index=i, stop_reason=trace.stop_reason, exit_reason=exit_reason)
        return trace

    @staticmethod
    def _prepare_home(config: dict, home: Path, workspace: Path) -> Path:
        """Point Hermes at this trial's private folders, with only the skills this config enables."""
        hermes_home = home / "hermes"
        for skill in config.get("skills", []):
            shutil.copytree(SKILLS_DIR / skill, hermes_home / "skills" / skill)
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["TERMINAL_CWD"] = str(workspace)
        os.chdir(workspace)
        return hermes_home

    @staticmethod
    def _toolsets(config: dict) -> list:
        return (list(config.get("tools", []))
                + (["memory"] if config.get("memory") else [])
                + (["skills"] if config.get("skills") else []))

    @staticmethod
    def _raise_if_provider_failed(result: dict, session: int):
        """A failed turn means the model API call itself failed (rate limit, 402 out of credits, 5xx, auth).
        That is infrastructure, not agent behaviour, so it is never graded."""
        if not result.get("failed"):
            return
        error = f"provider error in session {session}: {result.get('error') or result.get('failure_reason')}"
        if result.get("failure_retryable"):
            raise RetryableError(error)   # the runner retries with backoff
        raise ProviderError(error)        # the trial ends as "error"

    @staticmethod
    def _add_messages(trace: Trace, messages: list):
        """Convert Hermes' OpenAI-style message list into our trace events. Each Hermes message carries its own
        "timestamp", which we keep, so per-step durations in the UI are real."""
        for m in messages:
            ts = m.get("timestamp")
            if m["role"] == "user" and str(m.get("content") or "").startswith(LENGTH_NUDGE):
                trace.add("warning", ts=ts, name="truncated_continued",
                          content="Reply hit max_tokens; Hermes asked the model to continue.")
            elif m["role"] == "user":
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
