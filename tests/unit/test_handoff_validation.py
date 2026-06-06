"""
Unit tests for validate_handoff() in engine.py.

All tests use the investment_team fixture (george can_finalize=True,
warren can_finalize=False; george→warren, warren→george handoff rules).
No model calls are made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestra.config import load_config
from orchestra.engine import validate_handoff
from orchestra.errors import HandoffValidationError
from orchestra.schemas import AgentTurnResult, Handoff, HandoffType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "investment_team"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(
    agent_id: str,
    handoff_type: str,
    recipient: str,
    task: str | None = "next task",
) -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response="Some response.",
        handoff=Handoff(
            type=HandoffType(handoff_type),
            recipient=recipient,
            task=task,
        ),
    )


# ---------------------------------------------------------------------------
# Valid handoffs — must not raise
# ---------------------------------------------------------------------------


def test_valid_continue_from_george_to_warren() -> None:
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("george", "continue", "warren", "Dig deeper.")
    validate_handoff(result, team.agents["george"], team)  # no exception


def test_valid_return_from_warren_to_george() -> None:
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("warren", "return", "george", "Here are my findings.")
    validate_handoff(result, team.agents["warren"], team)  # no exception


def test_valid_final_from_george() -> None:
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("george", "final", "external", task=None)
    validate_handoff(result, team.agents["george"], team)  # no exception


def test_final_task_none_is_valid() -> None:
    """task=None is explicitly allowed for final handoffs."""
    team = load_config(FIXTURES / "team.yaml")
    result = AgentTurnResult(
        agent_id="george",
        response="Done.",
        handoff=Handoff(type=HandoffType.final, recipient="external", task=None),
    )
    validate_handoff(result, team.agents["george"], team)  # no exception


# ---------------------------------------------------------------------------
# Invalid handoffs — must raise HandoffValidationError
# ---------------------------------------------------------------------------


def test_final_by_non_finalizer_raises() -> None:
    """Warren has can_finalize=False; issuing 'final' must be rejected."""
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("warren", "final", "external", task=None)
    with pytest.raises(HandoffValidationError, match="can_finalize=False"):
        validate_handoff(result, team.agents["warren"], team)


def test_final_with_wrong_recipient_raises() -> None:
    """'final' handoff must have recipient='external'."""
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("george", "final", "warren", task=None)
    with pytest.raises(HandoffValidationError, match="external"):
        validate_handoff(result, team.agents["george"], team)


def test_handoff_to_unknown_agent_raises() -> None:
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("george", "continue", "elon", "Take over.")
    with pytest.raises(HandoffValidationError, match="unknown agent"):
        validate_handoff(result, team.agents["george"], team)


def test_handoff_to_self_raises() -> None:
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("george", "continue", "george", "Think again.")
    with pytest.raises(HandoffValidationError, match="itself"):
        validate_handoff(result, team.agents["george"], team)


def test_handoff_not_in_can_handoff_to_raises() -> None:
    """George's can_handoff_to=[warren]; routing to a hypothetical third agent fails."""
    team = load_config(FIXTURES / "team.yaml")
    # Temporarily fabricate a third agent id that exists in team.agents
    # by pointing to warren's config under a new key — simplest approach
    # is to check the error path using warren trying to route to warren
    # (which is caught by the self-handoff check first, so let's use a
    # modified fixture logic instead).
    #
    # The investment_team has only george and warren.  Warren's can_handoff_to
    # is [george].  We can test this rule by making warren try to route to
    # an agent that is NOT george.  We create a minimal ad-hoc team in memory.
    import yaml

    config_data = {
        "team": {"id": "t", "entry_agent": "alice", "default_energy": 10},
        "models": {"fake": {"provider": "fake", "model": "fake/default"}},
        "agents": {
            "alice": {
                "name": "Alice",
                "role_prompt": str(FIXTURES / "agents" / "george.md"),
                "model_profile": "fake",
                "can_finalize": True,
                "can_handoff_to": ["bob"],
            },
            "bob": {
                "name": "Bob",
                "role_prompt": str(FIXTURES / "agents" / "warren.md"),
                "model_profile": "fake",
                "can_finalize": False,
                "can_handoff_to": ["alice"],
            },
            "carol": {
                "name": "Carol",
                "role_prompt": str(FIXTURES / "agents" / "warren.md"),
                "model_profile": "fake",
                "can_finalize": True,
                "can_handoff_to": ["alice"],
            },
        },
    }
    import tempfile

    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False
    ) as f:
        yaml.dump(config_data, f)
        cfg_path = Path(f.name)

    team = load_config(cfg_path)
    cfg_path.unlink()

    # alice's can_handoff_to = ["bob"]; routing to carol should fail
    result = make_result("alice", "continue", "carol", "Task for Carol.")
    with pytest.raises(HandoffValidationError, match="not allowed"):
        validate_handoff(result, team.agents["alice"], team)


def test_can_handoff_to_none_allows_any_other_agent() -> None:
    """When can_handoff_to is None, the agent may route to any other agent."""
    import yaml
    import tempfile

    config_data = {
        "team": {"id": "t", "entry_agent": "alice", "default_energy": 10},
        "models": {"fake": {"provider": "fake", "model": "fake/default"}},
        "agents": {
            "alice": {
                "name": "Alice",
                "role_prompt": str(FIXTURES / "agents" / "george.md"),
                "model_profile": "fake",
                "can_finalize": True,
                # no can_handoff_to → None → unrestricted
            },
            "bob": {
                "name": "Bob",
                "role_prompt": str(FIXTURES / "agents" / "warren.md"),
                "model_profile": "fake",
                "can_finalize": False,
                "can_handoff_to": ["alice"],
            },
            "carol": {
                "name": "Carol",
                "role_prompt": str(FIXTURES / "agents" / "warren.md"),
                "model_profile": "fake",
                "can_finalize": True,
                "can_handoff_to": ["alice"],
            },
        },
    }
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False
    ) as f:
        yaml.dump(config_data, f)
        cfg_path = Path(f.name)

    team = load_config(cfg_path)
    cfg_path.unlink()

    # alice can_handoff_to=None → routing to carol should succeed
    result = make_result("alice", "continue", "carol", "Task for Carol.")
    validate_handoff(result, team.agents["alice"], team)  # no exception


def test_error_carries_agent_id() -> None:
    """HandoffValidationError must expose the failing agent's id."""
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("warren", "final", "external", task=None)
    with pytest.raises(HandoffValidationError) as exc_info:
        validate_handoff(result, team.agents["warren"], team)
    assert exc_info.value.agent_id == "warren"


def test_error_carries_handoff_object() -> None:
    """HandoffValidationError must expose the offending Handoff."""
    team = load_config(FIXTURES / "team.yaml")
    result = make_result("george", "continue", "nobody", "Task.")
    with pytest.raises(HandoffValidationError) as exc_info:
        validate_handoff(result, team.agents["george"], team)
    assert exc_info.value.handoff is not None
    assert exc_info.value.handoff.recipient == "nobody"
