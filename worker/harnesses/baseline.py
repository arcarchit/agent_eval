"""Baseline harness: a plain tool-calling loop written by hand (~100 lines).

It exists to prove the adapter interface works for a second harness, and as a simple
reference point to compare Hermes against.
"""
import json
import subprocess
from pathlib import Path

import openai

from common import ROOT, Model, Trace, call_model
from harnesses.base import Harness, sessions

SKILLS_DIR = ROOT / "skills"


def _tool(tool_name, description, **params):  # not "name": read_skill has a parameter called name
    return {"type": "function", "function": {
        "name": tool_name, "description": description,
        "parameters": {"type": "object", "properties": {k: {"type": "string"} for k in params},
                       "required": list(params)}}}


class Tools:
    """The tools the baseline agent can call. All file access is confined to the workspace."""

    def __init__(self, config: dict, workspace: Path, home: Path, trace: Trace):
        self.ws, self.trace = workspace, trace
        self.memory_file = home / "memory.json"
        self.skills = config.get("skills", [])
        self.schemas = []
        if "file" in config.get("tools", []):
            self.schemas += [_tool("read_file", "Read a file", path="relative path"),
                             _tool("write_file", "Write a file", path="relative path", content="file content"),
                             _tool("list_files", "List files in the workspace")]
        if "terminal" in config.get("tools", []):
            self.schemas.append(_tool("run_shell", "Run a shell command in the workspace", command="command"))
        if config.get("memory"):
            self.schemas += [_tool("memory_write", "Save a note that persists across sessions", note="the note"),
                             _tool("memory_read", "Read all saved notes")]
        if self.skills:
            self.schemas.append(_tool("read_skill", "Read a skill document", name="skill name"))

    def _path(self, rel: str) -> Path:
        p = (self.ws / rel).resolve()
        if not p.is_relative_to(self.ws.resolve()):
            raise ValueError("path escapes the workspace")
        return p

    def call(self, name: str, args: dict) -> str:
        if name == "read_file":
            return self._path(args["path"]).read_text()
        if name == "write_file":
            p = self._path(args["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return "ok"
        if name == "list_files":
            return "\n".join(str(p.relative_to(self.ws)) for p in sorted(self.ws.rglob("*")) if p.is_file())
        if name == "run_shell":
            r = subprocess.run(args["command"], shell=True, cwd=self.ws, capture_output=True, text=True, timeout=30)
            return (r.stdout + r.stderr)[-4000:]
        if name == "memory_write":
            notes = self._notes() + [args["note"]]
            self.memory_file.write_text(json.dumps(notes))
            self.trace.add("memory_write", content=args["note"])
            return "saved"
        if name == "memory_read":
            notes = self._notes()
            self.trace.add("memory_read", content="\n".join(notes))
            return "\n".join(notes) or "(no notes)"
        if name == "read_skill":
            if args["name"] not in self.skills:
                raise ValueError(f"unknown skill {args['name']}")
            self.trace.add("skill_used", name=args["name"])
            return (SKILLS_DIR / args["name"] / "SKILL.md").read_text()
        raise ValueError(f"unknown tool {name}")

    def _notes(self) -> list:
        return json.loads(self.memory_file.read_text()) if self.memory_file.exists() else []


class BaselineHarness(Harness):
    name = "baseline"

    def run(self, task, config, model: Model, workspace: Path, home: Path) -> Trace:
        trace = Trace()
        tools = Tools(config, workspace, home, trace)
        trace.available_tools = [s["function"]["name"] for s in tools.schemas]
        client = self.make_client(model)
        system = "You are a helpful agent. Use tools when useful. Finish with a short final answer."
        if tools.skills:
            system += "\nAvailable skills (read with read_skill): " + ", ".join(tools.skills)
        if config.get("memory"):
            system += "\nYou have persistent memory (memory_read / memory_write) shared across sessions."

        for i, message in enumerate(sessions(task)):
            trace.add("session_start", index=i)
            trace.stop_reason = "completed"
            trace.final_answer = self._session(client, model, config, system, message, tools, trace)
            trace.add("session_end", index=i, stop_reason=trace.stop_reason)
        return trace

    def make_client(self, model: Model):  # overridden in tests with a fake client
        return openai.OpenAI(base_url=model.base_url, api_key=model.api_key, max_retries=2)

    def _session(self, client, model, config, system, message, tools: Tools, trace: Trace) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": message}]
        trace.add("system", content=system)
        trace.add("user", content=message)
        for _ in range(config.get("max_steps", 10)):
            resp = call_model(client, model, config.get("temperature", 0.0), messages=messages,
                              tools=tools.schemas or openai.NOT_GIVEN, max_tokens=config.get("max_tokens", 1024))
            if resp.usage:
                trace.input_tokens += resp.usage.prompt_tokens
                trace.output_tokens += resp.usage.completion_tokens
            msg, finish = resp.choices[0].message, resp.choices[0].finish_reason
            calls = msg.tool_calls or []
            trace.add("assistant", content=msg.content or "", finish_reason=finish,
                      tool_calls=[{"name": c.function.name, "arguments": c.function.arguments} for c in calls])
            if finish == "length":
                # Same rule as Hermes: a cut-off reply may hold a half-written tool call, so don't run it.
                trace.add("warning", content=f"Reply cut off: hit max_tokens ({config.get('max_tokens', 1024)}). "
                                             "Incomplete reply not acted on; session ended.")
                trace.stop_reason = "truncated"
                return msg.content or ""
            messages.append(msg.model_dump(exclude_none=True))
            if not calls:
                return msg.content or ""
            for c in calls:
                trace.add("tool_call", name=c.function.name, arguments=c.function.arguments)
                try:
                    output, error = tools.call(c.function.name, json.loads(c.function.arguments or "{}")), False
                except Exception as e:  # bad args, unknown tool, file missing: the agent sees the error
                    output, error = f"ERROR: {e}", True
                trace.add("tool_result", name=c.function.name, output=str(output)[:4000], error=error)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": str(output)})
        trace.stop_reason = "step_limit"
        return ""
