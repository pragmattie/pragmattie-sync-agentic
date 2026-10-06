"""One strict wrapper for every Claude call the agents make.

What it enforces:

- The answer must be a JSON object matching the given schema. The request asks for it with
  ``output_config.format`` (structured outputs), and the reply is still parsed and checked here.
  An answer that isn't a JSON object (no text block, bad JSON, not an object, or cut off at
  ``max_tokens``) is retried once, then fails closed with ``LLMError("invalid_output", ...)``.
- A refusal fails at once with ``LLMError("refused", ...)``; it is never retried.
- SDK timeouts, rate limits and other API or connection errors become ``LLMError`` with the
  kinds ``timeout``, ``rate_limited`` and ``error``. The SDK client retries twice by itself first.
- Every result carries its tokens, cache use, latency, request id and cost, so each call can be
  audited and its cost reported.

No sampling parameter (``temperature``, ``top_p``, ``top_k``) is ever sent: Sonnet 5 rejects them
with a 400, and the schema, not sampling, is what keeps the answers consistent.

``effort`` goes inside ``output_config`` and is left out entirely when it is None, because
Haiku 4.5 rejects the field.

Schemas must not use ``minimum`` or ``maximum`` on numbers (the API rejects them). Limits like
these are enforced in code by the caller, after the call.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from sdlc.config import get_settings

LLM_ERROR_KINDS = ("timeout", "rate_limited", "refused", "invalid_output", "error")
OUTPUT_ATTEMPTS = 2

# USD per million tokens: input, output, cache write (5-minute ephemeral), cache read.
# Dated 2026-10-06. The claude-api skill could not be loaded when this was built, so these
# figures were NOT checked against it: confirm them before relying on reported costs.
PRICES_PER_MILLION: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-5-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
}


def cost_of(
    model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int
) -> float | None:
    """USD for one call, to 6 places, or None for a model with no price. It never guesses."""
    prices = PRICES_PER_MILLION.get(model)
    if prices is None:
        return None
    total = (
        input_tokens * prices["input"]
        + output_tokens * prices["output"]
        + cache_read * prices["cache_read"]
        + cache_write * prices["cache_write"]
    )
    return round(total / 1_000_000, 6)


class LLMError(Exception):
    def __init__(self, kind: str, message: str):
        if kind not in LLM_ERROR_KINDS:
            raise ValueError(f"Unknown LLMError kind: {kind}")
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
    request_id: str | None
    cost_usd: float | None


class StructuredLLM:
    def __init__(self, client: Any = None, model: str | None = None, max_tokens: int | None = None):
        settings = get_settings()
        self._client = client
        self.model = model or settings.risk_model
        self.max_tokens = max_tokens or settings.risk_max_output_tokens

    def _get_client(self) -> Any:
        if self._client is None:
            settings = get_settings()
            if not settings.anthropic_api_key:
                raise LLMError("error", "ANTHROPIC_API_KEY is not set.")
            self._client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key,
                max_retries=2,
                timeout=settings.agent_timeout_seconds,
            )
        return self._client

    def request_kwargs(
        self, system: str, user: str, schema: dict[str, Any], effort: str | None
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if effort is not None:
            output_config["effort"] = effort
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }

    def call(
        self, *, system: str, user: str, schema: dict[str, Any], effort: str | None = "medium"
    ) -> LLMResult:
        client = self._get_client()
        kwargs = self.request_kwargs(system, user, schema, effort)
        problem = ""
        for _ in range(OUTPUT_ATTEMPTS):
            started = time.monotonic()
            response = self._create(client, kwargs)
            latency_ms = int((time.monotonic() - started) * 1000)
            if response.stop_reason == "refusal":
                raise LLMError("refused", "The model refused to answer.")
            data, problem = _parse(response)
            if data is not None:
                return _result(response, data, self.model, latency_ms)
        raise LLMError("invalid_output", problem)

    @staticmethod
    def _create(client: Any, kwargs: dict[str, Any]) -> Any:
        # APITimeoutError subclasses APIConnectionError, so it must be caught first.
        try:
            return client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            raise LLMError("timeout", f"The request timed out: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("rate_limited", f"Rate limited: {exc}") from exc
        except (anthropic.APIConnectionError, anthropic.APIError) as exc:
            raise LLMError("error", f"API error: {exc}") from exc


def _parse(response: Any) -> tuple[dict[str, Any] | None, str]:
    """The answer as a dict, or None and what was wrong with it."""
    if response.stop_reason == "max_tokens":
        return None, "The answer was cut off at max_tokens."
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return None, "The answer had no text block."
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"The answer was not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "The answer was not a JSON object."
    return data, ""


def _result(response: Any, data: dict[str, Any], model: str, latency_ms: int) -> LLMResult:
    usage = response.usage
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0
    return LLMResult(
        data=data,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        latency_ms=latency_ms,
        request_id=getattr(response, "_request_id", None),
        cost_usd=cost_of(model, input_tokens, output_tokens, cache_read, cache_write),
    )
