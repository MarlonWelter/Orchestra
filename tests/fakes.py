"""
Test doubles for Orchestra components.

FakeModelClient is the primary fixture used by unit and integration tests.
It stores every request and returns pre-queued responses in order, making
it easy to assert on prompt content and simulate any model behaviour.
"""

from __future__ import annotations

from orchestra.schemas import ModelRequest, ModelResponse


class FakeModelClient:
    """
    Deterministic test double for ModelClient.

    Usage::

        fake = FakeModelClient(responses=[
            ModelResponse(structured_output=result_a),
            ModelResponse(structured_output=result_b),
        ])
        engine = Engine(model_client=fake)

    Each call to complete() pops one entry from the front of responses:
    - ModelResponse  → returned directly
    - BaseException  → raised directly (simulates provider errors)

    All ModelRequest objects received are appended to .requests for inspection.
    Raises RuntimeError if complete() is called after responses are exhausted.
    """

    def __init__(
        self,
        responses: list[ModelResponse | BaseException] | None = None,
    ) -> None:
        self.responses: list[ModelResponse | BaseException] = list(responses or [])
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError(
                f"FakeModelClient has no more responses queued. "
                f"Unexpected call #{len(self.requests)} for agent '{request.agent_id}' "
                f"(turn_id='{request.turn_id}')."
            )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response
