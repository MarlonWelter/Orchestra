"""
Integration tests: Engine + FakeModelClient end-to-end.

Five scenarios that validate the complete ADR-009 loop without any
network calls or API keys.  Each test uses a real TranscriptStore
pointed at tmp_path so on-disk output can also be inspected.

Scenarios
---------
1. Happy path       — 3-turn george→warren→george(final), all succeed.
2. Repair succeeds  — george returns bad JSON on first call, succeeds on repair.
3. Repair fails     — warren returns bad JSON twice; escalate to george who finalizes.
4. Exhaustion       — energy=2, george+warren each spend 1; turn 3 exhausts.
5. Invalid handoff  — george returns a handoff to itself; run fails immediately.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.config import load_config
from orchestra.engine import Engine
from orchestra.schemas import (
    AgentTurnResult,
    Handoff,
    HandoffType,
    ModelResponse,
    RunStatus,
    TurnStatus,
)
from orchestra.transcript_store import TranscriptStore
from tests.fakes import FakeModelClient

FIXTURES = Path(__file__).parent.parent / "fixtures" / "investment_team"


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def final_result(agent_id: str, response: str = "Final answer.") -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response=response,
        handoff=Handoff(type=HandoffType.final, recipient="external", task=None),
    )


def continue_result(
    agent_id: str, recipient: str, task: str, response: str = "Delegating."
) -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response=response,
        handoff=Handoff(type=HandoffType.continue_, recipient=recipient, task=task),
    )


def return_result(
    agent_id: str, recipient: str, task: str, response: str = "Done."
) -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response=response,
        handoff=Handoff(type=HandoffType.return_, recipient=recipient, task=task),
    )


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — george → warren → george(final)
# ---------------------------------------------------------------------------


def test_happy_path_three_turns(tmp_path: Path) -> None:
    team = load_config(FIXTURES / "team.yaml")
    fake = FakeModelClient(
        responses=[
            ModelResponse(
                structured_output=continue_result(
                    "george", "warren", "Perform deep value analysis."
                )
            ),
            ModelResponse(
                structured_output=return_result(
                    "warren",
                    "george",
                    "Synthesize and give final recommendation.",
                    response="Alphabet trades at 22x earnings. Strong buy.",
                )
            ),
            ModelResponse(
                structured_output=final_result(
                    "george",
                    "Based on Warren's analysis, we recommend a strong buy on Alphabet.",
                )
            ),
        ]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(team, "Should we buy Alphabet?", run_id="run_happy")

    # Run-level assertions
    assert run.status == RunStatus.completed
    assert run.final_answer == "Based on Warren's analysis, we recommend a strong buy on Alphabet."
    assert run.energy.used == 3
    assert run.energy.remaining == team.default_energy - 3

    # Turn index
    assert len(run.turns) == 3
    assert run.turns[0].agent_id == "george"
    assert run.turns[0].status == TurnStatus.completed
    assert run.turns[0].handoff_type == "continue"
    assert run.turns[0].recipient == "warren"

    assert run.turns[1].agent_id == "warren"
    assert run.turns[1].status == TurnStatus.completed
    assert run.turns[1].handoff_type == "return"
    assert run.turns[1].recipient == "george"

    assert run.turns[2].agent_id == "george"
    assert run.turns[2].status == TurnStatus.completed
    assert run.turns[2].handoff_type == "final"

    # All 3 model calls were made
    assert len(fake.requests) == 3
    assert fake.requests[0].agent_id == "george"
    assert fake.requests[1].agent_id == "warren"
    assert fake.requests[2].agent_id == "george"

    # Transcript files exist
    run_dir = tmp_path / "runs" / "run_happy"
    assert (run_dir / "run.json").exists()
    assert (run_dir / "turns" / "001-george.json").exists()
    assert (run_dir / "turns" / "002-warren.json").exists()
    assert (run_dir / "turns" / "003-george.json").exists()

    # run.json shows completed status
    run_data = json.loads((run_dir / "run.json").read_text())
    assert run_data["status"] == "completed"
    assert run_data["final_answer"] is not None


# ---------------------------------------------------------------------------
# Scenario 2: Repair succeeds — george returns invalid JSON, repair fixes it
# ---------------------------------------------------------------------------


def test_repair_succeeds(tmp_path: Path) -> None:
    team = load_config(FIXTURES / "team.yaml")
    fake = FakeModelClient(
        responses=[
            # First call: george returns malformed text
            ModelResponse(text="Sorry, here is my answer: blah blah not JSON"),
            # Repair call: george returns valid JSON
            ModelResponse(structured_output=final_result("george", "After repair: buy.")),
        ]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(team, "Buy or sell?", run_id="run_repair_ok")

    assert run.status == RunStatus.completed
    assert run.final_answer == "After repair: buy."

    # Two model calls (initial + repair)
    assert len(fake.requests) == 2
    assert fake.requests[0].turn_id == "001-george"
    assert fake.requests[1].turn_id == "001-george_repair"

    # 2 energy spent (1 initial + 1 repair)
    assert run.energy.used == 2

    # One completed turn with one repair_attempt recorded
    assert len(run.turns) == 1
    assert run.turns[0].status == TurnStatus.completed

    # Inspect the turn file for repair_attempts
    turn_file = tmp_path / "runs" / "run_repair_ok" / "turns" / "001-george.json"
    assert turn_file.exists()
    turn_data = json.loads(turn_file.read_text())
    assert len(turn_data["repair_attempts"]) == 1
    assert turn_data["repair_attempts"][0]["repair_valid"] is True


# ---------------------------------------------------------------------------
# Scenario 3: Repair fails + escalation — warren fails twice, george finalizes
# ---------------------------------------------------------------------------


def test_repair_fails_then_escalation_succeeds(tmp_path: Path) -> None:
    """
    Turn sequence:
      1. george succeeds → continue to warren
      2. warren initial call → invalid JSON
      3. warren repair call → still invalid JSON  (repair fails)
         → escalate to george
      4. george (escalated) → final
    """
    team = load_config(FIXTURES / "team.yaml")
    fake = FakeModelClient(
        responses=[
            # Turn 1 — george succeeds
            ModelResponse(
                structured_output=continue_result("george", "warren", "Run DCF model.")
            ),
            # Turn 2 — warren initial: bad text
            ModelResponse(text="I cannot produce JSON right now."),
            # Turn 2 — warren repair: still bad
            ModelResponse(text="Still not valid JSON, sorry."),
            # Turn 3 — george (escalated) finalizes
            ModelResponse(
                structured_output=final_result(
                    "george",
                    "Warren was unable to respond; based on existing context: buy.",
                )
            ),
        ]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(team, "Should we buy?", run_id="run_escalate")

    assert run.status == RunStatus.completed
    assert "buy" in run.final_answer

    # Four model calls total
    assert len(fake.requests) == 4

    # Turn index: turn 1 (george, completed), turn 2 (warren, failed), turn 3 (george, completed)
    assert len(run.turns) == 3
    assert run.turns[0].agent_id == "george"
    assert run.turns[0].status == TurnStatus.completed

    assert run.turns[1].agent_id == "warren"
    assert run.turns[1].status == TurnStatus.failed

    assert run.turns[2].agent_id == "george"
    assert run.turns[2].status == TurnStatus.completed
    assert run.turns[2].handoff_type == "final"

    # Warren's failed turn file should contain repair_attempts
    warren_turn_file = tmp_path / "runs" / "run_escalate" / "turns" / "002-warren.json"
    assert warren_turn_file.exists()
    warren_data = json.loads(warren_turn_file.read_text())
    assert warren_data["status"] == "failed"
    assert len(warren_data["repair_attempts"]) == 1
    assert warren_data["repair_attempts"][0]["repair_valid"] is False

    # George's escalated turn
    george_turn_file = tmp_path / "runs" / "run_escalate" / "turns" / "003-george.json"
    assert george_turn_file.exists()


# ---------------------------------------------------------------------------
# Scenario 4: Energy exhaustion
# ---------------------------------------------------------------------------


def test_energy_exhaustion(tmp_path: Path) -> None:
    """
    Energy=2. george spends 1, warren spends 1. Turn 3 sees energy=0 → exhaust.
    """
    import yaml

    config_data = {
        "team": {"id": "t", "entry_agent": "george", "default_energy": 2},
        "models": {"fake": {"provider": "fake", "model": "fake/default"}},
        "agents": {
            "george": {
                "name": "George",
                "role_prompt": str(FIXTURES / "agents" / "george.md"),
                "model_profile": "fake",
                "can_finalize": True,
                "can_handoff_to": ["warren"],
            },
            "warren": {
                "name": "Warren",
                "role_prompt": str(FIXTURES / "agents" / "warren.md"),
                "model_profile": "fake",
                "can_finalize": False,
                "can_handoff_to": ["george"],
            },
        },
    }
    cfg_file = tmp_path / "team.yaml"
    cfg_file.write_text(yaml.dump(config_data), encoding="utf-8")
    team = load_config(cfg_file)

    fake = FakeModelClient(
        responses=[
            # Turn 1 — george routes to warren (energy: 2→1)
            ModelResponse(
                structured_output=continue_result("george", "warren", "Run analysis.")
            ),
            # Turn 2 — warren routes back to george (energy: 1→0)
            ModelResponse(
                structured_output=return_result(
                    "warren", "george", "Compile report."
                )
            ),
            # Turn 3 would be george, but energy=0 → exhaust before any call
        ]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path / "runs"))
    run = engine.run(team, "Research Alphabet.", run_id="run_exhaust")

    assert run.status == RunStatus.exhausted
    assert run.energy.remaining == 0
    assert run.energy.used == 2
    # Only 2 model calls — turn 3 never made a call
    assert len(fake.requests) == 2
    # 2 completed turns in the index
    assert len(run.turns) == 2


# ---------------------------------------------------------------------------
# Scenario 5: Invalid handoff — george tries to hand off to itself
# ---------------------------------------------------------------------------


def test_invalid_handoff_fails_run(tmp_path: Path) -> None:
    team = load_config(FIXTURES / "team.yaml")
    fake = FakeModelClient(
        responses=[
            # george hands off to itself — invalid
            ModelResponse(
                structured_output=AgentTurnResult(
                    agent_id="george",
                    response="I'll handle it myself.",
                    handoff=Handoff(
                        type=HandoffType.continue_,
                        recipient="george",  # self-handoff → invalid
                        task="Analyze again.",
                    ),
                )
            ),
        ]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(team, "Analyze Alphabet.", run_id="run_bad_handoff")

    assert run.status == RunStatus.failed
    assert run.error is not None
    assert "itself" in run.error

    # One failed turn recorded
    assert len(run.turns) == 1
    assert run.turns[0].status == TurnStatus.failed

    # Turn file exists and captures the parse result and error
    turn_file = tmp_path / "runs" / "run_bad_handoff" / "turns" / "001-george.json"
    assert turn_file.exists()
    turn_data = json.loads(turn_file.read_text())
    assert turn_data["status"] == "failed"
    assert "itself" in turn_data["error"]
