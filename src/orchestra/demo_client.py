"""
DemoModelClient — a scripted ModelClient for demonstrating Orchestra's CLI.

Used automatically when every model profile in the team config uses
provider: fake.  Agents produce minimal, deterministic responses that
complete the run without any network calls or API keys, so the full
turn cycle and Rich output format can be explored immediately.

Non-finalizing agents hand off to their first allowed recipient.
Finalizing agents return 'final' with a placeholder response.
"""

from __future__ import annotations

from orchestra.config import TeamConfig
from orchestra.schemas import (
    AgentTurnResult,
    Handoff,
    HandoffType,
    ModelRequest,
    ModelResponse,
)


class DemoModelClient:
    """
    Scripted client that drives any valid team to completion without a model.

    This is NOT a test double — it lives in the production package so the
    CLI can use it for demonstration purposes.  For deterministic unit tests,
    use tests.fakes.FakeModelClient instead.
    """

    def __init__(self, team: TeamConfig) -> None:
        self._team = team

    def complete(self, request: ModelRequest) -> ModelResponse:
        agent = self._team.agents[request.agent_id]

        placeholder = (
            f"[Demo] {agent.name} would respond here. "
            f"Configure a real model profile and run again for actual AI responses."
        )

        if agent.can_finalize:
            result = AgentTurnResult(
                agent_id=request.agent_id,
                response=placeholder,
                handoff=Handoff(
                    type=HandoffType.final,
                    recipient="external",
                    task=None,
                ),
            )
        else:
            # Route to the first allowed recipient
            recipients = agent.can_handoff_to or [
                a.id
                for a in self._team.agents.values()
                if a.id != request.agent_id
            ]
            recipient_id = recipients[0]
            recipient_name = self._team.agents[recipient_id].name
            result = AgentTurnResult(
                agent_id=request.agent_id,
                response=placeholder,
                handoff=Handoff(
                    type=HandoffType.continue_,
                    recipient=recipient_id,
                    task=f"Demo task delegated to {recipient_name}.",
                ),
            )

        return ModelResponse(structured_output=result)
