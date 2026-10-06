import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from sdlc.agents.llm import PRICES_PER_MILLION, LLMError, StructuredLLM, cost_of
from sdlc.config import Settings, get_settings

SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer"}},
    "required": ["score"],
    "additionalProperties": False,
}
MODEL = "claude-sonnet-5"


def _response(text=None, stop_reason="end_turn", blocks=None):
    if blocks is None:
        blocks = [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=5000,
            cache_creation_input_tokens=800,
        ),
        _request_id="req_123",
    )


class FakeClient:
    """Returns (or raises) the queued replies in order, and keeps every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _llm(*replies):
    client = FakeClient(*replies)
    return StructuredLLM(client=client, model=MODEL, max_tokens=500), client


def _call(llm, effort="medium"):
    return llm.call(system="You score PRs.", user="Score this.", schema=SCHEMA, effort=effort)


@pytest.fixture
def settings_env(monkeypatch):
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_good_answer_returns_data_tokens_and_cost():
    llm, client = _llm(_response(json.dumps({"score": 7})))

    result = _call(llm)

    assert result.data == {"score": 7}
    assert result.model == MODEL
    assert (result.input_tokens, result.output_tokens) == (1000, 200)
    assert (result.cache_read_tokens, result.cache_write_tokens) == (5000, 800)
    assert result.request_id == "req_123"
    assert result.latency_ms >= 0
    assert result.cost_usd == cost_of(MODEL, 1000, 200, 5000, 800)
    assert result.cost_usd == 0.007
    assert len(client.requests) == 1


def test_bad_json_then_good_json_succeeds_on_retry():
    llm, client = _llm(_response("not json"), _response('{"score": 3}'))

    assert _call(llm).data == {"score": 3}
    assert len(client.requests) == 2


def test_retry_reports_both_attempts_tokens_and_cost():
    llm, _ = _llm(_response("not json"), _response('{"score": 3}'))

    result = _call(llm)

    assert (result.input_tokens, result.output_tokens) == (2000, 400)
    assert (result.cache_read_tokens, result.cache_write_tokens) == (10000, 1600)
    assert result.cost_usd == 0.014


def test_bad_json_twice_raises_invalid_output():
    llm, client = _llm(_response("not json"), _response("{still not"))

    with pytest.raises(LLMError) as err:
        _call(llm)

    assert err.value.kind == "invalid_output"
    assert "not valid JSON" in err.value.message
    assert len(client.requests) == 2


def test_last_problem_is_reported():
    llm, _ = _llm(_response("not json"), _response("[1, 2]"))

    with pytest.raises(LLMError) as err:
        _call(llm)

    assert err.value.kind == "invalid_output"
    assert err.value.message == "The answer was not a JSON object."


def test_no_text_block_is_invalid_output():
    no_text = _response(blocks=[SimpleNamespace(type="thinking", thinking="hmm")])
    llm, _ = _llm(no_text, no_text)

    with pytest.raises(LLMError) as err:
        _call(llm)

    assert err.value.kind == "invalid_output"
    assert "no text block" in err.value.message


def test_max_tokens_reached_is_invalid_output():
    cut_off = _response('{"score": 7}', stop_reason="max_tokens")
    llm, client = _llm(cut_off, cut_off)

    with pytest.raises(LLMError) as err:
        _call(llm)

    assert err.value.kind == "invalid_output"
    assert "max_tokens" in err.value.message
    assert len(client.requests) == 2


def test_refusal_raises_refused_without_retry():
    llm, client = _llm(_response("", stop_reason="refusal"), _response('{"score": 1}'))

    with pytest.raises(LLMError) as err:
        _call(llm)

    assert err.value.kind == "refused"
    assert len(client.requests) == 1


_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status):
    return cls("boom", response=httpx.Response(status, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (anthropic.APITimeoutError(request=_REQUEST), "timeout"),
        (_status_error(anthropic.RateLimitError, 429), "rate_limited"),
        (anthropic.APIConnectionError(request=_REQUEST), "error"),
        (_status_error(anthropic.InternalServerError, 500), "error"),
        (_status_error(anthropic.BadRequestError, 400), "error"),
    ],
)
def test_sdk_errors_map_to_kinds(error, kind):
    llm, client = _llm(error)

    with pytest.raises(LLMError) as err:
        _call(llm)

    assert err.value.kind == kind
    assert len(client.requests) == 1


def test_missing_key_is_reported_only_on_call(settings_env):
    settings_env.setenv("ANTHROPIC_API_KEY", "")

    llm = StructuredLLM()

    with pytest.raises(LLMError) as err:
        _call(llm)
    assert err.value.kind == "error"
    assert err.value.message == "ANTHROPIC_API_KEY is not set."


def test_settings_defaults_and_key_hidden_from_repr(settings_env):
    settings_env.setenv("ANTHROPIC_API_KEY", "sk-test-secret")

    settings = Settings()

    assert settings.anthropic_api_key == "sk-test-secret"
    assert "sk-test-secret" not in repr(settings)
    assert settings.risk_model == "claude-sonnet-5"
    assert settings.triage_model == "claude-haiku-4-5-20251001"
    assert settings.risk_max_output_tokens == 1500
    assert settings.agent_timeout_seconds == 60


def test_defaults_come_from_settings(settings_env):
    settings_env.setenv("RISK_MODEL", "claude-sonnet-5-5")
    settings_env.setenv("RISK_MAX_OUTPUT_TOKENS", "900")

    llm = StructuredLLM(client=FakeClient())

    assert (llm.model, llm.max_tokens) == ("claude-sonnet-5-5", 900)


def test_lazy_client_uses_key_retries_and_timeout(settings_env):
    settings_env.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    settings_env.setenv("AGENT_TIMEOUT_SECONDS", "45")

    client = StructuredLLM()._get_client()

    assert isinstance(client, anthropic.Anthropic)
    assert client.max_retries == 2
    assert client.timeout == 45


def test_request_has_no_sampling_parameters():
    llm, client = _llm(_response('{"score": 7}'))

    _call(llm)

    for kwargs in (client.requests[0], llm.request_kwargs("s", "u", SCHEMA, "high")):
        assert not {"temperature", "top_p", "top_k"} & kwargs.keys()


def test_request_is_exact():
    llm, client = _llm(_response('{"score": 7}'))

    _call(llm, effort="high")

    assert client.requests[0] == {
        "model": MODEL,
        "max_tokens": 500,
        "system": [
            {"type": "text", "text": "You score PRs.", "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": "Score this."}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "high"},
    }


def test_effort_absent_when_none():
    kwargs = StructuredLLM(client=FakeClient()).request_kwargs("s", "u", SCHEMA, None)

    assert kwargs["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert "effort" not in kwargs


def test_effort_defaults_to_medium():
    llm, client = _llm(_response('{"score": 7}'))

    llm.call(system="s", user="u", schema=SCHEMA)

    assert client.requests[0]["output_config"]["effort"] == "medium"


def test_system_block_is_cached():
    kwargs = StructuredLLM(client=FakeClient()).request_kwargs("sys", "u", SCHEMA, "low")

    assert kwargs["system"] == [
        {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}
    ]


@pytest.mark.parametrize("model", sorted(PRICES_PER_MILLION))
def test_cost_of_each_priced_model(model):
    prices = PRICES_PER_MILLION[model]

    assert cost_of(model, 1_000_000, 0, 0, 0) == prices["input"]
    assert cost_of(model, 0, 1_000_000, 0, 0) == prices["output"]
    assert cost_of(model, 0, 0, 1_000_000, 0) == prices["cache_read"]
    assert cost_of(model, 0, 0, 0, 1_000_000) == prices["cache_write"]
    expected = round(
        (
            123 * prices["input"]
            + 45 * prices["output"]
            + 6789 * prices["cache_read"]
            + 10 * prices["cache_write"]
        )
        / 1_000_000,
        6,
    )
    assert cost_of(model, 123, 45, 6789, 10) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-5", 0.012),
        ("claude-sonnet-5-5", 0.012),
        ("claude-haiku-4-5-20251001", 0.006),
        ("claude-haiku-4-5", 0.006),
    ],
)
def test_cost_of_matches_the_official_rates(model, expected):
    # 1,000 input + 1,000 output tokens, hard-coded so a wrong price fails.
    assert cost_of(model, 1000, 1000, 0, 0) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [("claude-sonnet-5", 0.0027), ("claude-haiku-4-5-20251001", 0.00135)],
)
def test_cost_of_cache_rates(model, expected):
    # 1,000 cache-write + 1,000 cache-read tokens.
    assert cost_of(model, 0, 0, 1000, 1000) == expected


def test_priced_models_are_exactly_the_checked_ones():
    assert set(PRICES_PER_MILLION) == {
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
    }


def test_cost_of_unknown_model_is_none():
    assert cost_of("claude-unknown-9", 1000, 200, 0, 0) is None


def test_unknown_error_kind_is_rejected():
    with pytest.raises(ValueError):
        LLMError("oops", "bad kind")
