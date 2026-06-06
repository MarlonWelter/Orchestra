"""
LiteLLMClient — ModelClient implementation backed by the LiteLLM library.

LiteLLM provides a single unified API for 100+ model providers (OpenAI,
Anthropic, Bedrock, Vertex, Ollama, etc.) with a consistent interface.
This module wraps it behind the ModelClient Protocol so the engine and
the rest of the codebase never touch LiteLLM directly.

Model string format
-------------------
LiteLLM expects model strings in the form "<provider>/<model>":

    "openai/gpt-4o"
    "anthropic/claude-3-5-sonnet-20241022"
    "ollama_chat/llama3"

We derive this from the team config's provider + model fields:
    provider: openai  +  model: gpt-4o  →  "openai/gpt-4o"

Structured output
-----------------
We pass `response_format=AgentTurnResult` (the Pydantic class) to LiteLLM.
LiteLLM converts it to the appropriate provider-specific format:
- OpenAI (gpt-4o and newer): JSON schema via the structured-output API
- Older OpenAI models: json_object mode with the schema in the prompt
- Other providers: LiteLLM handles translation where possible

The response text is then parsed into AgentTurnResult locally.  For
providers that return a `.parsed` attribute (OpenAI structured output),
we use that directly.  The engine's repair path handles any parse failures.

Error mapping
-------------
LiteLLM exceptions are mapped to ModelProviderError with is_retryable set
appropriately (True for transient errors, False for permanent failures).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from orchestra.config import ModelProfileConfig
from orchestra.errors import ModelProviderError
from orchestra.schemas import (
    AgentTurnResult,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)

# Opt out of LiteLLM's telemetry before any call is made
litellm.telemetry = False  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# LiteLLM exception type names that represent transient (retryable) failures
_RETRYABLE_ERRORS = frozenset(
    {
        "RateLimitError",
        "APIConnectionError",
        "ServiceUnavailableError",
        "Timeout",
        "APITimeoutError",
        "InternalServerError",
        "ContextWindowExceededError",  # transient in practice (can retry with shorter input)
    }
)

# LiteLLM exception type names that represent permanent failures
_PERMANENT_ERRORS = frozenset(
    {
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
    }
)


class LiteLLMClient:
    """
    ModelClient backed by the LiteLLM library.

    Receives the team's model profiles dict so it can resolve model_profile
    names to provider/model strings and per-profile settings (temperature,
    max_tokens, timeout_seconds).

    Usage::

        client = LiteLLMClient(team.models)
        response = client.complete(request)

    Raises:
        ModelProviderError: on any provider/network error.
    """

    def __init__(self, models: dict[str, ModelProfileConfig]) -> None:
        self._models = models

    def complete(self, request: ModelRequest) -> ModelResponse:
        """
        Send one completion request via LiteLLM and return a ModelResponse.

        Raises:
            ModelProviderError: on any provider/network error, including
                auth failures, rate limits, and connection errors.
        """
        profile = self._models.get(request.model_profile)
        if profile is None:
            raise ModelProviderError(
                f"Unknown model profile '{request.model_profile}'. "
                f"Available profiles: {sorted(self._models)}",
            )

        if profile.provider == "fake":
            raise ModelProviderError(
                f"Profile '{request.model_profile}' uses provider 'fake', "
                f"which is not supported by LiteLLMClient. "
                f"Use DemoModelClient for teams with fake providers.",
                provider="fake",
                model=profile.model,
                is_retryable=False,
            )

        model_string = f"{profile.provider}/{profile.model}"

        # Per-request overrides take precedence over profile defaults
        temperature = (
            request.temperature
            if request.temperature is not None
            else profile.temperature
        )
        max_tokens = (
            request.max_tokens
            if request.max_tokens is not None
            else profile.max_tokens
        )
        timeout = (
            request.timeout_seconds
            if request.timeout_seconds is not None
            else profile.timeout_seconds
        )

        kwargs: dict[str, Any] = {
            "model": model_string,
            "messages": request.messages,
            "response_format": AgentTurnResult,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout

        logger.debug(
            "LiteLLM call: model=%s agent=%s turn=%s",
            model_string,
            request.agent_id,
            request.turn_id,
        )

        try:
            raw = litellm.completion(**kwargs)
        except Exception as exc:
            raise _map_litellm_error(exc, profile) from exc

        return _parse_litellm_response(raw)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _map_litellm_error(
    exc: Exception, profile: ModelProfileConfig
) -> ModelProviderError:
    """
    Convert a LiteLLM exception into a ModelProviderError.

    is_retryable is True for transient network/rate-limit errors and
    False for permanent failures (auth, bad request, etc.).
    """
    exc_type = type(exc).__name__
    message = str(exc)

    # Permanent errors take precedence over the retryable set
    if exc_type in _PERMANENT_ERRORS:
        is_retryable = False
    elif exc_type in _RETRYABLE_ERRORS:
        is_retryable = True
    else:
        # Unknown exception type — treat as non-retryable to be safe
        is_retryable = False

    return ModelProviderError(
        f"LiteLLM error ({exc_type}): {message}",
        provider=profile.provider,
        model=profile.model,
        is_retryable=is_retryable,
    )


def _parse_litellm_response(raw: Any) -> ModelResponse:
    """
    Convert a raw LiteLLM completion object to Orchestra's ModelResponse.

    Parse order:
      1. message.parsed   — populated by LiteLLM for OpenAI structured output
      2. message.content  — JSON text to parse ourselves

    On parse failure, structured_output is left as None so the engine's
    repair path can request a corrected response.
    """
    choice = raw.choices[0]
    message = choice.message
    content: str = message.content or ""

    # Usage
    usage_data = getattr(raw, "usage", None)
    usage = ModelUsage()
    if usage_data is not None:
        usage = ModelUsage(
            input_tokens=getattr(usage_data, "prompt_tokens", None),
            output_tokens=getattr(usage_data, "completion_tokens", None),
            total_tokens=getattr(usage_data, "total_tokens", None),
        )

    # Structured output — prefer .parsed (set by LiteLLM for OpenAI structured output)
    structured: AgentTurnResult | None = None
    parsed_attr = getattr(message, "parsed", None)
    if isinstance(parsed_attr, AgentTurnResult):
        structured = parsed_attr
    elif content.strip():
        try:
            data = json.loads(content)
            structured = AgentTurnResult.model_validate(data)
        except Exception:
            pass  # engine repair path handles this

    return ModelResponse(
        text=content,
        structured_output=structured,
        usage=usage,
        model=getattr(raw, "model", ""),
        provider=None,
        raw_response=None,  # omit raw to keep transcript files lean
        finish_reason=getattr(choice, "finish_reason", None),
    )
