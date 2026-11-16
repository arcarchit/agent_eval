"""Small shared pieces: the model registry, the trace recorder and the retryable error."""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("EVALS_ROOT", Path(__file__).resolve().parent.parent))


class RetryableError(Exception):
    """Infrastructure problem (rate limit, 5xx, network). The API retries the trial with backoff
    instead of scoring it as a failure, so provider outages do not corrupt results."""


@dataclass
class Model:
    name: str
    provider_model: str
    base_url: str
    api_key: str
    price_in: float   # USD per 1M input tokens
    price_out: float  # USD per 1M output tokens

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000


def load_models() -> dict:
    return yaml.safe_load((ROOT / "models.yaml").read_text())


def get_model(name: str) -> Model:
    spec = load_models()["models"][name]
    return Model(
        name=name,
        provider_model=spec["provider_model"],
        base_url=spec["base_url"],
        api_key=os.environ.get(spec["api_key_env"], ""),
        price_in=spec["price_per_mtok"]["input"],
        price_out=spec["price_per_mtok"]["output"],
    )


@dataclass
class Trace:
    """Everything that happened in one trial. Harness adapters fill this in.

    Event types: session_start, user, assistant, tool_call, tool_result,
    memory_read, memory_write, skill_used.
    """
    events: list = field(default_factory=list)
    final_answer: str = ""
    stop_reason: str = "completed"  # completed | step_limit | token_limit | error
    steps: int = 0                  # model calls in the scored session
    input_tokens: int = 0
    output_tokens: int = 0
    available_tools: list = field(default_factory=list)

    def add(self, type: str, **data):
        self.events.append({"type": type, "ts": round(time.time(), 3), **data})
