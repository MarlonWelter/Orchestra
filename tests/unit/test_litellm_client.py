"""
Unit tests for LiteLLMClient.

All tests mock litellm.completion — no network calls, no API keys.

Coverage:
  - Successful response with JSON text → parsed into AgentTurnResult
  - Successful response with .parsed attribute (OpenAI structured output)
  - Empty / unparseable response → structured_output=None (repair path)
  - Model profile resolution (provider/model string composition)
  - Per-profile temperature / max_tokens / timeout forwarded to litellm
  - Per-request overrides take precedence over profile defaults
  - Unknown model profile → ModelProviderError
  - Fake provider in LiteLLMClient → ModelProviderError
  - Retryable provider errors (rate limit, connection) → is_retryable=True
  - Permanent provider errors (auth, bad request) → is_retryable=False
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestra.config import ModelProfileConfig
from orchestra.errors import ModelProviderError
from orchestra.litellm_client import LiteLLMClient, _map_litellm_error, _parse_litellm_response
from orchestra.schemas import (
    AgentTurnResult,
    Handoff,
    HandoffType,
    ModelRequest,
    ModelResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_profile(
    provider: str = "openai",
    model: str = "gpt-4o",
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
) -> ModelProfileConfig:
    return ModelProfileConfig(
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )


def make_request(
    model_profile: str = "frontier",
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
) -> ModelRequest:
    return ModelRequest(
        run_id="run_test",
        turn_id="001-george",
        agent_id="george",
        model_profile=model_profile,
        messages=[{"role": "user", "content": "Hello."}],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )


def make_agent_turn_result(agent_id: str = "george") -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response="Buy Alphabet.",
        handoff=Handoff(type=HandoffType.final, recipient="external", task=None),
    )


def make_litellm_raw(
    content: str,
    finish_reason: str = "stop",
    parsed: AgentTurnResult | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> MagicMock:
    """Build a mock object that mimics a LiteLLM completion response."""
    message = MagicMock()
    message.content = content
    message.parsed = parsed

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens

    raw = MagicMock()
    raw.choices = [choice]
    raw.usage = usage
    raw.model = "gpt-4o-2024-08-06"
    return raw


# ---------------------------------------------------------------------------
# Successful responses — text parsing
# ---------------------------------------------------------------------------


def test_json_text_response_is_parsed_into_structured_output() -> None:
    result = make_agent_turn_result()
    raw = make_litellm_raw(content=result.model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    assert response.structured_output is not None
    assert response.structured_output.agent_id == "george"
    assert response.structured_output.handoff.type == HandoffType.final


def test_parsed_attribute_used_directly_when_present() -> None:
    """When LiteLLM sets message.parsed, we use it without re-parsing."""
    result = make_agent_turn_result()
    # .parsed is the Pydantic object; content is irrelevant (trust .parsed)
    raw = make_litellm_raw(content="{}", parsed=result)

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    assert response.structured_output is result


def test_empty_response_yields_none_structured_output() -> None:
    raw = make_litellm_raw(content="")

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    assert response.structured_output is None
    assert response.text == ""


def test_invalid_json_yields_none_structured_output() -> None:
    raw = make_litellm_raw(content="Sorry, I cannot produce JSON right now.")

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    assert response.structured_output is None
    assert "Sorry" in response.text


def test_valid_json_but_invalid_schema_yields_none_structured_output() -> None:
    bad_json = json.dumps({"agent_id": "george", "response": "", "handoff": {}})
    raw = make_litellm_raw(content=bad_json)

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    # response is empty string → Pydantic validator rejects it
    assert response.structured_output is None


# ---------------------------------------------------------------------------
# Model profile resolution
# ---------------------------------------------------------------------------


def test_model_string_composed_from_provider_and_model() -> None:
    profile = make_profile(provider="anthropic", model="claude-3-5-sonnet-20241022")
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"claude": profile})
        client.complete(make_request(model_profile="claude"))

    called_model = mock_call.call_args.kwargs["model"]
    assert called_model == "anthropic/claude-3-5-sonnet-20241022"


def test_unknown_model_profile_raises_model_provider_error() -> None:
    with patch("orchestra.litellm_client.litellm.completion"):
        client = LiteLLMClient({"frontier": make_profile()})
        with pytest.raises(ModelProviderError, match="Unknown model profile"):
            client.complete(make_request(model_profile="nonexistent"))


def test_fake_provider_raises_model_provider_error() -> None:
    profile = make_profile(provider="fake", model="fake/default")
    with patch("orchestra.litellm_client.litellm.completion"):
        client = LiteLLMClient({"fake": profile})
        with pytest.raises(ModelProviderError, match="fake"):
            client.complete(make_request(model_profile="fake"))


# ---------------------------------------------------------------------------
# Parameter forwarding
# ---------------------------------------------------------------------------


def test_profile_temperature_forwarded_to_litellm() -> None:
    profile = make_profile(temperature=0.2)
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"frontier": profile})
        client.complete(make_request())

    assert mock_call.call_args.kwargs["temperature"] == pytest.approx(0.2)


def test_profile_max_tokens_forwarded_to_litellm() -> None:
    profile = make_profile(max_tokens=500)
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"frontier": profile})
        client.complete(make_request())

    assert mock_call.call_args.kwargs["max_tokens"] == 500


def test_profile_timeout_forwarded_to_litellm() -> None:
    profile = make_profile(timeout_seconds=30)
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"frontier": profile})
        client.complete(make_request())

    assert mock_call.call_args.kwargs["timeout"] == 30


def test_none_profile_temperature_not_forwarded() -> None:
    """If profile.temperature is None, 'temperature' must not appear in kwargs."""
    profile = make_profile(temperature=None)
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"frontier": profile})
        client.complete(make_request())

    assert "temperature" not in mock_call.call_args.kwargs


def test_request_temperature_overrides_profile() -> None:
    profile = make_profile(temperature=0.5)
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"frontier": profile})
        client.complete(make_request(temperature=0.1))

    assert mock_call.call_args.kwargs["temperature"] == pytest.approx(0.1)


def test_response_format_is_agent_turn_result_class() -> None:
    """The Pydantic class itself must be passed as response_format (per ADR-008)."""
    raw = make_litellm_raw(content=make_agent_turn_result().model_dump_json())

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw) as mock_call:
        client = LiteLLMClient({"frontier": make_profile()})
        client.complete(make_request())

    assert mock_call.call_args.kwargs["response_format"] is AgentTurnResult


# ---------------------------------------------------------------------------
# Usage / metadata
# ---------------------------------------------------------------------------


def test_usage_populated_from_litellm_response() -> None:
    raw = make_litellm_raw(
        content=make_agent_turn_result().model_dump_json(),
        prompt_tokens=120,
        completion_tokens=60,
    )

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 60
    assert response.usage.total_tokens == 180


def test_finish_reason_populated() -> None:
    raw = make_litellm_raw(
        content=make_agent_turn_result().model_dump_json(),
        finish_reason="stop",
    )

    with patch("orchestra.litellm_client.litellm.completion", return_value=raw):
        client = LiteLLMClient({"frontier": make_profile()})
        response = client.complete(make_request())

    assert response.finish_reason == "stop"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _make_exc(type_name: str, message: str = "error") -> Exception:
    """Create a mock exception whose type name matches type_name."""
    exc_cls = type(type_name, (Exception,), {})
    return exc_cls(message)


def test_rate_limit_error_is_retryable() -> None:
    exc = _make_exc("RateLimitError", "Rate limit exceeded")
    profile = make_profile()
    err = _map_litellm_error(exc, profile)
    assert err.is_retryable is True


def test_api_connection_error_is_retryable() -> None:
    exc = _make_exc("APIConnectionError", "Connection failed")
    err = _map_litellm_error(exc, make_profile())
    assert err.is_retryable is True


def test_service_unavailable_is_retryable() -> None:
    exc = _make_exc("ServiceUnavailableError", "Service down")
    err = _map_litellm_error(exc, make_profile())
    assert err.is_retryable is True


def test_authentication_error_is_not_retryable() -> None:
    exc = _make_exc("AuthenticationError", "Invalid API key")
    err = _map_litellm_error(exc, make_profile())
    assert err.is_retryable is False


def test_bad_request_error_is_not_retryable() -> None:
    exc = _make_exc("BadRequestError", "Invalid request")
    err = _map_litellm_error(exc, make_profile())
    assert err.is_retryable is False


def test_unknown_error_is_not_retryable() -> None:
    exc = _make_exc("SomeUnknownError", "Unexpected")
    err = _map_litellm_error(exc, make_profile())
    assert err.is_retryable is False


def test_model_provider_error_carries_provider_and_model() -> None:
    exc = _make_exc("AuthenticationError", "Bad key")
    profile = make_profile(provider="openai", model="gpt-4o")
    err = _map_litellm_error(exc, profile)
    assert err.provider == "openai"
    assert err.model == "gpt-4o"


def test_provider_error_raised_on_litellm_exception() -> None:
    """When litellm.completion raises, complete() raises ModelProviderError."""
    exc = _make_exc("AuthenticationError", "Bad API key")

    with patch("orchestra.litellm_client.litellm.completion", side_effect=exc):
        client = LiteLLMClient({"frontier": make_profile()})
        with pytest.raises(ModelProviderError, match="AuthenticationError"):
            client.complete(make_request())
