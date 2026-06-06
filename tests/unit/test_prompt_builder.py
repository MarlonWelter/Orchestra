"""
Tests for prompt_builder.py.

Verifies the three-message structure, content of each message,
history formatting, and first-turn / external-sender semantics.
No model calls are made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestra.config import load_config
from orchestra.errors import PromptAssemblyError
from orchestra.prompt_builder import PromptBuilder
from orchestra.schemas import EnergyState, TurnRecord, TurnStatus, ValidationResult

FIXTURES = Path(__file__).parent.parent / "fixtures" / "investment_team"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_energy(remaining: int = 14, initial: int = 20) -> EnergyState:
    return EnergyState(initial=initial, used=initial - remaining, remaining=remaining)


def make_turn_record(
    index: int,
    sender: str,
    agent: str,
    task: str,
    response: str,
    handoff_type: str,
    recipient: str,
    run_id: str = "run_test",
) -> TurnRecord:
    return TurnRecord(
        turn_id=f"{index:03d}-{agent}",
        run_id=run_id,
        index=index,
        agent_id=agent,
        sender_agent_id=sender,
        status=TurnStatus.completed,
        logical_input={"task": task},
        parsed_result={
            "agent_id": agent,
            "response": response,
            "handoff": {"type": handoff_type, "recipient": recipient, "task": task},
        },
        validation=ValidationResult(valid=True),
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_build_messages_returns_three_messages() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze Alphabet.",
        sender_agent_id="external",
        energy=make_energy(20, 20),
    )
    assert len(messages) == 3


def test_message_roles_are_correct() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze Alphabet.",
        sender_agent_id="external",
        energy=make_energy(20, 20),
    )
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "system"
    assert messages[2]["role"] == "user"


# ---------------------------------------------------------------------------
# System prompt (message 0)
# ---------------------------------------------------------------------------


def test_system_prompt_contains_agent_turn_result_schema() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    system = messages[0]["content"]
    assert "AgentTurnResult" in system or "agent_id" in system
    assert "continue" in system
    assert "return" in system
    assert "final" in system


def test_system_prompt_contains_team_roster() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    system = messages[0]["content"]
    assert "george" in system
    assert "warren" in system
    assert "George" in system
    assert "Warren" in system


def test_system_prompt_marks_finalizer() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    system = messages[0]["content"]
    assert "Can finalize" in system


def test_system_prompt_contains_energy_guidance() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    system = messages[0]["content"]
    assert "energy" in system.lower()


# ---------------------------------------------------------------------------
# Role description (message 1)
# ---------------------------------------------------------------------------


def test_role_description_contains_agent_role_prompt_content() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()

    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    role = messages[1]["content"]
    assert "George" in role


def test_role_description_is_separate_from_system_prompt() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages_george = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    messages_warren = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="george",
        energy=make_energy(),
    )
    # System prompts should be identical for the same team
    assert messages_george[0]["content"] == messages_warren[0]["content"]
    # Role descriptions should differ
    assert messages_george[1]["content"] != messages_warren[1]["content"]
    assert "Warren" in messages_warren[1]["content"]


def test_missing_role_prompt_raises_prompt_assembly_error(tmp_path: Path) -> None:
    """Role prompt file removed after config load → PromptAssemblyError."""
    import shutil
    import yaml

    # Write a valid config with existing files
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "george.md").write_text("# George\nManager.", encoding="utf-8")
    (agents_dir / "warren.md").write_text("# Warren\nAnalyst.", encoding="utf-8")
    config_data = {
        "team": {"id": "t", "entry_agent": "george", "default_energy": 10},
        "models": {"fake": {"provider": "fake", "model": "fake/default"}},
        "agents": {
            "george": {
                "role_prompt": "agents/george.md",
                "model_profile": "fake",
                "can_finalize": True,
                "can_handoff_to": ["warren"],
            },
            "warren": {
                "role_prompt": "agents/warren.md",
                "model_profile": "fake",
                "can_finalize": False,
                "can_handoff_to": ["george"],
            },
        },
    }
    config_path = tmp_path / "team.yaml"
    config_path.write_text(yaml.dump(config_data), encoding="utf-8")
    team = load_config(config_path)

    # Now delete George's role prompt file
    (agents_dir / "george.md").unlink()

    builder = PromptBuilder()
    with pytest.raises(PromptAssemblyError, match="george"):
        builder.build_messages(
            team=team,
            active_agent=team.agents["george"],
            turn_history=[],
            current_task="Analyze.",
            sender_agent_id="external",
            energy=make_energy(),
        )


# ---------------------------------------------------------------------------
# Current input (message 2)
# ---------------------------------------------------------------------------


def test_current_input_contains_active_agent() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=[],
        current_task="Analyze Alphabet.",
        sender_agent_id="george",
        energy=make_energy(14),
    )
    current = messages[2]["content"]
    assert "warren" in current
    assert "Warren" in current


def test_current_input_contains_sender() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=[],
        current_task="Analyze Alphabet.",
        sender_agent_id="george",
        energy=make_energy(14),
    )
    current = messages[2]["content"]
    assert "george" in current
    assert "George" in current


def test_current_input_first_turn_shows_external_sender() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze Alphabet.",
        sender_agent_id="external",
        energy=make_energy(20, 20),
    )
    current = messages[2]["content"]
    assert "external" in current


def test_current_input_contains_task() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    task = "Analyze Alphabet and decide whether to add to the position."
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task=task,
        sender_agent_id="external",
        energy=make_energy(20, 20),
    )
    current = messages[2]["content"]
    assert task in current


def test_current_input_contains_energy() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="george",
        energy=make_energy(14, 20),
    )
    current = messages[2]["content"]
    assert "14" in current
    assert "20" in current


def test_current_input_contains_allowed_recipients() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    current = messages[2]["content"]
    # George can hand off to Warren
    assert "warren" in current or "Warren" in current


def test_current_input_contains_can_finalize() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()

    messages_george = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    messages_warren = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="george",
        energy=make_energy(),
    )
    assert "true" in messages_george[2]["content"]
    assert "false" in messages_warren[2]["content"]


def test_current_input_ends_with_json_reminder() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="external",
        energy=make_energy(),
    )
    current = messages[2]["content"]
    assert current.strip().endswith("Return only a valid AgentTurnResult JSON object.")


# ---------------------------------------------------------------------------
# History formatting
# ---------------------------------------------------------------------------


def test_no_history_shows_no_prior_turns_message() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=[],
        current_task="Analyze.",
        sender_agent_id="george",
        energy=make_energy(),
    )
    current = messages[2]["content"]
    assert "No prior turns" in current


def test_history_appears_as_transcript_text() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()

    history = [
        make_turn_record(
            index=1,
            sender="external",
            agent="george",
            task="Analyze Alphabet.",
            response="I'll ask Warren.",
            handoff_type="continue",
            recipient="warren",
        )
    ]
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=history,
        current_task="Analyze from a value perspective.",
        sender_agent_id="george",
        energy=make_energy(19, 20),
    )
    current = messages[2]["content"]
    assert "[Turn 1]" in current
    assert "external → george" in current
    assert "I'll ask Warren." in current
    assert "continue → warren" in current


def test_history_with_multiple_turns() -> None:
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()

    history = [
        make_turn_record(1, "external", "george", "Task A", "Resp A", "continue", "warren"),
        make_turn_record(2, "george", "warren", "Task B", "Resp B", "return", "george"),
    ]
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["george"],
        turn_history=history,
        current_task="Finalize.",
        sender_agent_id="warren",
        energy=make_energy(18, 20),
    )
    current = messages[2]["content"]
    assert "[Turn 1]" in current
    assert "[Turn 2]" in current
    assert "Resp A" in current
    assert "Resp B" in current


def test_history_is_not_alternating_messages() -> None:
    """History must be plain text, not synthetic role messages."""
    team = load_config(FIXTURES / "team.yaml")
    builder = PromptBuilder()

    history = [
        make_turn_record(1, "external", "george", "Task A", "Resp A", "continue", "warren"),
    ]
    messages = builder.build_messages(
        team=team,
        active_agent=team.agents["warren"],
        turn_history=history,
        current_task="Task B.",
        sender_agent_id="george",
        energy=make_energy(19, 20),
    )
    # History must appear inside the user message, not as extra list entries
    assert len(messages) == 3
    assert "[Turn 1]" in messages[2]["content"]
