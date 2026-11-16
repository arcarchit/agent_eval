"""The harness adapter interface. This is the only thing a new harness has to implement."""
from abc import ABC, abstractmethod
from pathlib import Path

from common import Model, Trace


class Harness(ABC):
    name: str

    @abstractmethod
    def run(self, task: dict, config: dict, model: Model, workspace: Path, home: Path) -> Trace:
        """Run one task and return its trace.

        task      - the task dict (prompt, optional memory_setup messages, ...)
        config    - harness settings: skills, memory, tools, temperature, max_steps, max_tokens
        workspace - empty temp dir (plus the task's setup files) the agent works in
        home      - temp dir for harness state (memory, skills). Fresh per trial = isolation.

        Sessions: each message in task["memory_setup"] runs as its own session first, then the
        prompt runs in a NEW session. Only memory (if enabled) can carry facts across them.
        Raise common.RetryableError for rate limits / provider outages.
        """


def sessions(task: dict) -> list:
    return list(task.get("memory_setup", [])) + [task["prompt"]]
