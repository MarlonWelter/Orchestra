"""
ModelClient — the boundary between the engine and any model provider.

The engine only depends on this Protocol. LiteLLM, fake, or any other
backend can be swapped in without touching orchestration logic.

Production rule: no module outside this file and tests/fakes.py may import
a concrete client implementation. The engine always receives a ModelClient
through constructor injection.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orchestra.schemas import ModelRequest, ModelResponse


@runtime_checkable
class ModelClient(Protocol):
    """Minimal interface every model backend must satisfy."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        """
        Send one model call and return the normalized response.

        Raises:
            ModelProviderError: on provider/network failure.
            InvalidOutputError: if the response cannot be parsed into
                                AgentTurnResult (raised by implementations
                                that do local validation).
        """
        ...
