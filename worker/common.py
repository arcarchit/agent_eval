"""Small shared pieces: the model registry, calling a model, the trace recorder, and the two error types."""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import openai
import yaml

ROOT = Path(os.environ.get("EVALS_ROOT", Path(__file__).resolve().parent.parent))


class RetryableError(Exception):
    """Infrastructure problem (rate limit, 5xx, network). The API retries the trial with backoff
    instead of scoring it as a failure, so provider outages do not corrupt results."""


class ProviderError(Exception):
    """Model API failed in a way retrying will not fix (HTTP 402 out of credits, 401 bad key,
    unknown model). The trial ends as status "error": excluded from statistics, shown as a count."""


@dataclass
class Model:
    name: str
    provider_model: str
    base_url: str
    api_key: str
    price_in: float   # USD per 1M input tokens
    price_out: float  # USD per 1M output tokens
    routing: dict | None = None  # OpenRouter provider routing (models.yaml `provider_routing`), sent as "provider"

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000

    def request_params(self, temperature: float) -> dict:
        """Request fields that EVERY harness (and the judge) sends the same way, so two configs in a comparison
        differ only in what the experiment varies. `routing` pins who serves the model on OpenRouter, e.g. only
        full-precision providers, no silent fallback to a quantized one."""
        params: dict = {"temperature": temperature}
        if self.routing:
            params["extra_body"] = {"provider": self.routing}
        return params


def call_model(client, model: Model, temperature: float, **kwargs):
    """One chat-completions call with the fields every caller must send the same way, and provider errors
    mapped to our two error types. Used by the baseline harness and the judge (Hermes makes its own calls,
    but receives the same request_params)."""
    try:
        return client.chat.completions.create(model=model.provider_model, **model.request_params(temperature), **kwargs)
    except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
        raise RetryableError(str(e)) from e                                 # the runner retries with backoff
    except openai.APIStatusError as e:                                      # 401 key, 402 credits, 404 model
        raise ProviderError(f"HTTP {e.status_code}: {e.message}") from e   # the trial ends as "error"


def load_models() -> dict:
    return yaml.safe_load((ROOT / "models.yaml").read_text())


def get_model(name: str, spec: dict | None = None) -> Model:
    """`spec` is the snapshot stored in the config (preferred, so a config always means the same model).
    Without it (CLI, judge), the current models.yaml entry is used."""
    spec = spec or load_models()["models"][name]
    return Model(
        name=name,
        provider_model=spec["provider_model"],
        base_url=spec["base_url"],
        api_key=os.environ.get(spec["api_key_env"], ""),
        price_in=spec["price_per_mtok"]["input"],
        price_out=spec["price_per_mtok"]["output"],
        routing=spec.get("provider_routing"),
    )


@dataclass
class Trace:
    """Everything that happened in one trial. Harness adapters fill this in.

    Event types: session_start, system (the system prompt sent), user, assistant, tool_call, tool_result,
    memory_read, memory_write, skill_used, warning (turn ended early, e.g. reply hit max_tokens), session_end.
    """
    events: list = field(default_factory=list)
    final_answer: str = ""
    stop_reason: str = "completed"  # completed | step_limit | loop_guard | truncated (hit max_tokens) | error
    input_tokens: int = 0
    output_tokens: int = 0
    available_tools: list = field(default_factory=list)

    def add(self, type: str, ts: float | None = None, **data):
        """ts = when the event finished (seconds). The UI shows the gap to the previous event as its duration,
        so an "assistant" event's duration is the model call's latency. Adapters that only see the result
        afterwards (Hermes) pass the harness's own timestamps."""
        self.events.append({"type": type, "ts": round(ts if ts else time.time(), 3), **data})

    def llm_calls(self) -> int:
        return sum(e["type"] == "assistant" for e in self.events)

    def turns(self) -> int:  # user turns = sessions
        return sum(e["type"] == "session_start" for e in self.events)
