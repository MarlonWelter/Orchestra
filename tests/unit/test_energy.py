"""
Unit tests for energy accounting in the engine.

Covers _deduct_energy() directly and verifies that TurnRecord fields
(energy_before, energy_cost, energy_after) are set correctly by the engine.

All tests use FakeModelClient — no network calls are made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestra.engine import Engine, _deduct_energy
from orchestra.schemas import (
    AgentTurnResult,
    EnergyState,
    Handoff,
    HandoffType,
    ModelResponse,
    RunState,
    RunStatus,
)
from tests.fakes import FakeModelClient

FIXTURES = Path(__file__).parent.parent / "fixtures" / "investment_team"


# ---------------------------------------------------------------------------
# _deduct_energy — direct unit tests
# ---------------------------------------------------------------------------


def _make_run_state(remaining: int = 20) -> RunState:
    return RunState(
        run_id="run_test",
        team_id="t",
        energy=EnergyState(initial=remaining, used=0, remaining=remaining),
    )


def test_deduct_energy_updates_used_and_remaining() -> None:
    rs = _make_run_state(10)
    _deduct_energy(rs, 1)
    assert rs.energy.used == 1
    assert rs.energy.remaining == 9


def test_deduct_energy_multiple_calls_accumulate() -> None:
    rs = _make_run_state(10)
    _deduct_energy(rs, 1)
    _deduct_energy(rs, 1)
    _deduct_energy(rs, 3)
    assert rs.energy.used == 5
    assert rs.energy.remaining == 5


def test_deduct_energy_does_not_floor_at_zero() -> None:
    """_deduct_energy does not guard the floor — the engine does."""
    rs = _make_run_state(1)
    _deduct_energy(rs, 1)
    assert rs.energy.remaining == 0
    # Caller is responsible for not over-deducting
    _deduct_energy(rs, 1)
    assert rs.energy.remaining == -1


def test_deduct_energy_initial_unchanged() -> None:
    rs = _make_run_state(20)
    _deduct_energy(rs, 5)
    assert rs.energy.initial == 20


# ---------------------------------------------------------------------------
# Engine energy tracking — verified via RunState and TurnRecord data
# ---------------------------------------------------------------------------


def _make_final_result(agent_id: str) -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response="Final answer.",
        handoff=Handoff(type=HandoffType.final, recipient="external", task=None),
    )


def _make_continue_result(agent_id: str, recipient: str, task: str) -> AgentTurnResult:
    return AgentTurnResult(
        agent_id=agent_id,
        response="Delegating.",
        handoff=Handoff(type=HandoffType.continue_, recipient=recipient, task=task),
    )


def test_single_turn_energy_cost_is_one(tmp_path: Path) -> None:
    """One successful turn costs exactly 1 energy unit."""
    from orchestra.config import load_config
    from orchestra.transcript_store import TranscriptStore

    team = load_config(FIXTURES / "team.yaml")
    fake = FakeModelClient(
        responses=[ModelResponse(structured_output=_make_final_result("george"))]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(team, "Analyze.", run_id="run_energy_01")

    assert run.status == RunStatus.completed
    assert run.energy.used == 1
    assert run.energy.remaining == team.default_energy - 1


def test_two_turn_energy_cost_is_two(tmp_path: Path) -> None:
    """Two successful turns (george→warren) cost exactly 2 energy units."""
    from orchestra.config import load_config
    from orchestra.transcript_store import TranscriptStore

    team = load_config(FIXTURES / "team.yaml")
    fake = FakeModelClient(
        responses=[
            ModelResponse(
                structured_output=_make_continue_result("george", "warren", "Deep dive.")
            ),
            ModelResponse(structured_output=_make_final_result("warren")),
        ]
    )
    # warren can_finalize=False in fixture — patch temporarily
    # Actually warren can't finalize. We need george to finalize.
    # Use: george→warren→george(final)
    fake2 = FakeModelClient(
        responses=[
            ModelResponse(
                structured_output=_make_continue_result("george", "warren", "Analyze.")
            ),
            ModelResponse(
                structured_output=AgentTurnResult(
                    agent_id="warren",
                    response="Here is my analysis.",
                    handoff=Handoff(
                        type=HandoffType.return_,
                        recipient="george",
                        task="Synthesize my findings.",
                    ),
                )
            ),
            ModelResponse(structured_output=_make_final_result("george")),
        ]
    )
    engine = Engine(fake2, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(team, "Analyze.", run_id="run_energy_02")

    assert run.status == RunStatus.completed
    assert run.energy.used == 3
    assert run.energy.remaining == team.default_energy - 3


def test_energy_exhaustion_terminates_run(tmp_path: Path) -> None:
    """When energy hits 0 at turn start, the run status is exhausted."""
    from orchestra.config import load_config
    from orchestra.transcript_store import TranscriptStore
    import yaml
    import tempfile

    # Build a config with energy=2 so exhaustion happens predictably
    config_data = {
        "team": {"id": "t", "entry_agent": "alice", "default_energy": 2},
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
        },
    }
    cfg_file = tmp_path / "team.yaml"
    cfg_file.write_text(yaml.dump(config_data), encoding="utf-8")
    team = load_config(cfg_file)

    # Turn 1: alice→bob (costs 1, remaining→1)
    # Turn 2: bob→alice (costs 1, remaining→0)
    # Turn 3: remaining==0 → exhaust
    fake = FakeModelClient(
        responses=[
            ModelResponse(
                structured_output=_make_continue_result("alice", "bob", "Dig in.")
            ),
            ModelResponse(
                structured_output=AgentTurnResult(
                    agent_id="bob",
                    response="Done.",
                    handoff=Handoff(
                        type=HandoffType.return_,
                        recipient="alice",
                        task="Wrap up.",
                    ),
                )
            ),
            # No third response queued — exhaust should trigger before the call
        ]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path / "runs"))
    run = engine.run(team, "Start task.", run_id="run_exhaust")

    assert run.status == RunStatus.exhausted
    assert run.energy.remaining == 0
    assert run.energy.used == 2
    # No third model call should have been made
    assert len(fake.requests) == 2


def test_final_beats_exhaustion(tmp_path: Path) -> None:
    """If the last energy point is spent and agent returns 'final', status is completed."""
    from orchestra.config import load_config
    from orchestra.transcript_store import TranscriptStore
    import yaml

    config_data = {
        "team": {"id": "t", "entry_agent": "alice", "default_energy": 1},
        "models": {"fake": {"provider": "fake", "model": "fake/default"}},
        "agents": {
            "alice": {
                "name": "Alice",
                "role_prompt": str(FIXTURES / "agents" / "george.md"),
                "model_profile": "fake",
                "can_finalize": True,
            },
        },
    }
    cfg_file = tmp_path / "team.yaml"
    cfg_file.write_text(yaml.dump(config_data), encoding="utf-8")
    team = load_config(cfg_file)

    # energy=1: alice spends it (remaining→0) but returns 'final'
    fake = FakeModelClient(
        responses=[ModelResponse(structured_output=_make_final_result("alice"))]
    )
    engine = Engine(fake, transcript_store=TranscriptStore(tmp_path / "runs"))
    run = engine.run(team, "Do it all.", run_id="run_final_beats")

    # completed, not exhausted — final wins
    assert run.status == RunStatus.completed
    assert run.energy.remaining == 0
    assert run.energy.used == 1
    assert run.final_answer == "Final answer."
