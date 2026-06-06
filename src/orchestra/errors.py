"""
Orchestra exception hierarchy.

Every error carries enough context to be useful in transcripts and logs.
Callers should catch the most specific type they can handle; the engine
catches OrchestraError as a fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestra.schemas import Handoff, ModelResponse


class OrchestraError(Exception):
    """Base class for all Orchestra errors."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(OrchestraError):
    """Raised when team configuration is invalid or cannot be loaded."""


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class PromptAssemblyError(OrchestraError):
    """Raised when PromptBuilder cannot construct a valid messages list."""


# ---------------------------------------------------------------------------
# Model provider
# ---------------------------------------------------------------------------


class ModelProviderError(OrchestraError):
    """
    Raised when the model provider returns an error or times out.

    is_retryable=True means the engine may retry once (transient failures).
    Authentication and quota errors are not retryable.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        is_retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.is_retryable = is_retryable


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------


class InvalidOutputError(OrchestraError):
    """
    Raised when the model response cannot be parsed into AgentTurnResult.

    Carries the raw content and the validation error so both can be stored
    in the turn transcript and used to construct a repair prompt.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_content: str = "",
        validation_error: str = "",
        model_response: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_content = raw_content
        self.validation_error = validation_error
        self.model_response = model_response


# ---------------------------------------------------------------------------
# Handoff validation
# ---------------------------------------------------------------------------


class HandoffValidationError(OrchestraError):
    """
    Raised when an AgentTurnResult contains an invalid handoff.

    Carries the reason and the offending handoff so the engine can write
    a useful failed-turn record.
    """

    def __init__(
        self,
        message: str,
        *,
        agent_id: str = "",
        handoff: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.agent_id = agent_id
        self.handoff = handoff


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------


class EnergyExhaustedError(OrchestraError):
    """Raised when the energy budget reaches zero and another turn is needed."""


# ---------------------------------------------------------------------------
# Transcript storage
# ---------------------------------------------------------------------------


class TranscriptWriteError(OrchestraError):
    """
    Raised when a transcript write fails.

    Non-fatal by default — the engine logs the error and continues.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.original_error = original_error
