"""
Tests for transcript_store.py.

All tests use tmp_path so nothing is written to the real filesystem.
No model calls are made.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.schemas import (
    EnergyState,
    RunState,
    RunStatus,
    TurnRecord,
    TurnStatus,
    TurnSummary,
    ValidationResult,
)
from orchestra.transcript_store import TranscriptStore, generate_run_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store(tmp_path: Path) -> TranscriptStore:
    return TranscriptStore(base_dir=tmp_path / ".orchestra")


def make_run_state(run_id: str = "run_test_001") -> RunState:
    return RunState(
        run_id=run_id,
        team_id="test_team",
        status=RunStatus.running,
        external_input="Analyze something.",
        entry_agent="george",
        started_at="2026-06-06T14:00:00+00:00",
        energy=EnergyState(initial=20, used=0, remaining=20),
    )


def make_turn_record(
    run_id: str = "run_test_001",
    index: int = 1,
    agent_id: str = "george",
    sender_agent_id: str = "external",
) -> TurnRecord:
    return TurnRecord(
        turn_id=f"{index:03d}-{agent_id}",
        run_id=run_id,
        index=index,
        agent_id=agent_id,
        sender_agent_id=sender_agent_id,
        status=TurnStatus.completed,
        started_at="2026-06-06T14:00:01+00:00",
        completed_at="2026-06-06T14:00:05+00:00",
        energy_before=20,
        energy_cost=1,
        energy_after=19,
        logical_input={"task": "Analyze something.", "sender": "external"},
        parsed_result={
            "agent_id": "george",
            "response": "I'll delegate to Warren.",
            "handoff": {"type": "continue", "recipient": "warren", "task": "Analyze."},
        },
        validation=ValidationResult(valid=True),
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# generate_run_id
# ---------------------------------------------------------------------------


def test_generate_run_id_format() -> None:
    run_id = generate_run_id()
    assert run_id.startswith("run_")
    parts = run_id.split("_")
    assert len(parts) == 4  # run, YYYYMMDD, HHMMSS, hex
    assert len(parts[3]) == 4  # 2 bytes = 4 hex chars


def test_generate_run_id_unique() -> None:
    ids = {generate_run_id() for _ in range(20)}
    assert len(ids) == 20  # all unique (probabilistically certain)


# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------


def test_start_run_creates_directories(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)

    run_dir = tmp_path / ".orchestra" / "runs" / run.run_id
    turns_dir = run_dir / "turns"
    assert run_dir.is_dir()
    assert turns_dir.is_dir()


def test_start_run_writes_run_json(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)

    run_json = tmp_path / ".orchestra" / "runs" / run.run_id / "run.json"
    assert run_json.exists()
    data = read_json(run_json)
    assert data["run_id"] == run.run_id
    assert data["status"] == "running"
    assert data["team_id"] == "test_team"
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# write_turn
# ---------------------------------------------------------------------------


def test_write_turn_creates_turn_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)

    turn = make_turn_record(run_id=run.run_id)
    store.write_turn(turn)

    turn_file = (
        tmp_path / ".orchestra" / "runs" / run.run_id / "turns" / "001-george.json"
    )
    assert turn_file.exists()


def test_write_turn_file_contains_correct_fields(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)

    turn = make_turn_record(run_id=run.run_id)
    store.write_turn(turn)

    turn_file = (
        tmp_path / ".orchestra" / "runs" / run.run_id / "turns" / "001-george.json"
    )
    data = read_json(turn_file)
    assert data["agent_id"] == "george"
    assert data["sender_agent_id"] == "external"
    assert data["index"] == 1
    assert data["status"] == "completed"
    assert data["energy_before"] == 20
    assert data["energy_cost"] == 1
    assert data["energy_after"] == 19


def test_write_multiple_turns_creates_multiple_files(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)

    store.write_turn(make_turn_record(run_id=run.run_id, index=1, agent_id="george"))
    store.write_turn(
        make_turn_record(
            run_id=run.run_id,
            index=2,
            agent_id="warren",
            sender_agent_id="george",
        )
    )

    turns_dir = tmp_path / ".orchestra" / "runs" / run.run_id / "turns"
    files = sorted(f.name for f in turns_dir.iterdir())
    assert files == ["001-george.json", "002-warren.json"]


# ---------------------------------------------------------------------------
# update_run
# ---------------------------------------------------------------------------


def test_update_run_reflects_new_turn_in_index(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)

    turn = make_turn_record(run_id=run.run_id)
    store.write_turn(turn)

    run.turns.append(
        TurnSummary(
            turn_id=turn.turn_id,
            index=turn.index,
            agent_id=turn.agent_id,
            status=turn.status,
            handoff_type="continue",
            recipient="warren",
            path=f"turns/{turn.turn_id}.json",
        )
    )
    store.update_run(run)

    run_json = tmp_path / ".orchestra" / "runs" / run.run_id / "run.json"
    data = read_json(run_json)
    assert len(data["turns"]) == 1
    assert data["turns"][0]["agent_id"] == "george"


# ---------------------------------------------------------------------------
# complete_run
# ---------------------------------------------------------------------------


def test_complete_run_sets_status_and_final_answer(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)
    store.complete_run(run, final_answer="The answer is 42.")

    run_json = tmp_path / ".orchestra" / "runs" / run.run_id / "run.json"
    data = read_json(run_json)
    assert data["status"] == "completed"
    assert data["final_answer"] == "The answer is 42."
    assert data["completed_at"] is not None
    # In-memory state is also updated
    assert run.status == RunStatus.completed
    assert run.final_answer == "The answer is 42."


# ---------------------------------------------------------------------------
# fail_run
# ---------------------------------------------------------------------------


def test_fail_run_sets_status_and_error(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)
    store.fail_run(run, error="Warren returned unparseable output after repair.")

    run_json = tmp_path / ".orchestra" / "runs" / run.run_id / "run.json"
    data = read_json(run_json)
    assert data["status"] == "failed"
    assert "Warren" in data["error"]
    assert data["completed_at"] is not None
    assert run.status == RunStatus.failed


# ---------------------------------------------------------------------------
# exhaust_run
# ---------------------------------------------------------------------------


def test_exhaust_run_sets_status(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)
    store.exhaust_run(run)

    run_json = tmp_path / ".orchestra" / "runs" / run.run_id / "run.json"
    data = read_json(run_json)
    assert data["status"] == "exhausted"
    assert data["completed_at"] is not None
    assert run.status == RunStatus.exhausted


# ---------------------------------------------------------------------------
# Incremental write behaviour
# ---------------------------------------------------------------------------


def test_run_json_valid_after_first_turn_before_completion(tmp_path: Path) -> None:
    """Partial transcript must be valid JSON at any point during a run."""
    store = make_store(tmp_path)
    run = make_run_state()
    store.start_run(run)
    store.write_turn(make_turn_record(run_id=run.run_id))
    store.update_run(run)

    # run.json is readable and has status "running" — run not yet finished
    run_json = tmp_path / ".orchestra" / "runs" / run.run_id / "run.json"
    data = read_json(run_json)
    assert data["status"] == "running"
