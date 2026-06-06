"""
Tests for the Orchestra CLI (cli.py).

Uses Typer's CliRunner so commands are invoked in-process without
spawning a subprocess.  No network calls or API keys required —
the fake provider triggers DemoModelClient automatically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from orchestra.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent.parent / "fixtures" / "investment_team"
EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "investment_team"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_team_config(
    tmp_path: Path,
    *,
    num_agents: int = 1,
    entry_can_finalize: bool = True,
) -> Path:
    """Write a minimal fake-provider team config and return its path."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "alice.md").write_text("# Alice\nEntry agent.", encoding="utf-8")

    config: dict = {
        "team": {
            "id": "t",
            "entry_agent": "alice",
            "default_energy": 10,
        },
        "models": {"fake": {"provider": "fake", "model": "fake/default"}},
        "agents": {
            "alice": {
                "name": "Alice",
                "role_prompt": "agents/alice.md",
                "model_profile": "fake",
                "can_finalize": entry_can_finalize,
            }
        },
    }

    if num_agents == 2:
        (agents_dir / "bob.md").write_text("# Bob\nSecond agent.", encoding="utf-8")
        config["agents"]["alice"]["can_handoff_to"] = ["bob"]
        config["agents"]["bob"] = {
            "name": "Bob",
            "role_prompt": "agents/bob.md",
            "model_profile": "fake",
            "can_finalize": False,
            "can_handoff_to": ["alice"],
        }

    cfg_path = tmp_path / "team.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    return cfg_path


def make_real_provider_team_config(tmp_path: Path) -> Path:
    """Write a config that uses a non-fake provider."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "alice.md").write_text("# Alice\nEntry agent.", encoding="utf-8")

    config = {
        "team": {"id": "t", "entry_agent": "alice", "default_energy": 10},
        "models": {"frontier": {"provider": "openai", "model": "gpt-4o"}},
        "agents": {
            "alice": {
                "name": "Alice",
                "role_prompt": "agents/alice.md",
                "model_profile": "frontier",
                "can_finalize": True,
            }
        },
    }
    cfg_path = tmp_path / "team.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# orchestra validate — success cases
# ---------------------------------------------------------------------------


def test_validate_fixture_team_exits_zero() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert result.exit_code == 0


def test_validate_shows_config_loaded_message() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert "Config loaded" in result.output or "OK" in result.output


def test_validate_shows_team_name() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert "Investment Team" in result.output


def test_validate_shows_entry_agent() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert "george" in result.output


def test_validate_shows_all_agents() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert "george" in result.output
    assert "warren" in result.output


def test_validate_shows_model_profiles() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert "fake" in result.output


def test_validate_shows_can_finalize_flag() -> None:
    result = runner.invoke(app, ["validate", str(FIXTURES / "team.yaml")])
    assert "can-finalize" in result.output


def test_validate_example_team_exits_zero() -> None:
    """The examples/investment_team config must be syntactically valid."""
    result = runner.invoke(app, ["validate", str(EXAMPLES / "team.yaml")])
    assert result.exit_code == 0


def test_validate_example_team_shows_four_agents() -> None:
    result = runner.invoke(app, ["validate", str(EXAMPLES / "team.yaml")])
    for agent in ("george", "warren", "elon", "klaus"):
        assert agent in result.output


# ---------------------------------------------------------------------------
# orchestra validate — error cases
# ---------------------------------------------------------------------------


def test_validate_missing_file_exits_nonzero() -> None:
    result = runner.invoke(app, ["validate", "does_not_exist.yaml"])
    assert result.exit_code != 0


def test_validate_missing_file_shows_error_message() -> None:
    result = runner.invoke(app, ["validate", "does_not_exist.yaml"])
    assert "not found" in result.output.lower()


def test_validate_invalid_config_exits_nonzero(tmp_path: Path) -> None:
    bad_cfg = tmp_path / "bad.yaml"
    bad_cfg.write_text(
        "team:\n  id: t\n  entry_agent: nobody\n  default_energy: 5\n"
        "models:\n  fake:\n    provider: fake\n    model: fake/default\n"
        "agents: {}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(bad_cfg)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# orchestra run — fake provider (DemoModelClient)
# ---------------------------------------------------------------------------


def test_run_with_fake_provider_exits_zero(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code == 0


def test_run_shows_completed_status(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert "completed" in result.output


def test_run_shows_final_answer_panel(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert "Final Answer" in result.output


def test_run_shows_turn_log(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert "Turn Log" in result.output
    assert "alice" in result.output


def test_run_shows_header_with_team_name(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert "Orchestra Run" in result.output


def test_run_shows_energy_summary(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert "Energy:" in result.output


def test_run_writes_transcript_to_output_dir(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    out_dir = tmp_path / "transcripts"
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(out_dir)],
    )
    assert result.exit_code == 0
    # At least one run directory should exist under out_dir/runs/
    runs_dir = out_dir / "runs"
    assert runs_dir.exists()
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "run.json").exists()


def test_run_with_explicit_run_id(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "run",
            str(cfg),
            "Test question",
            "--run-id",
            "run_test_explicit",
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0
    assert (out_dir / "runs" / "run_test_explicit" / "run.json").exists()


def test_run_transcript_has_completed_status(tmp_path: Path) -> None:
    cfg = make_fake_team_config(tmp_path)
    out_dir = tmp_path / "out"
    runner.invoke(
        app,
        [
            "run",
            str(cfg),
            "My question",
            "--run-id",
            "run_cli_test",
            "--output-dir",
            str(out_dir),
        ],
    )
    run_json = out_dir / "runs" / "run_cli_test" / "run.json"
    data = json.loads(run_json.read_text())
    assert data["status"] == "completed"
    assert data["external_input"] == "My question"


def test_run_fixture_team_exits_zero(tmp_path: Path) -> None:
    """The investment_team fixture (fake provider) must complete successfully."""
    result = runner.invoke(
        app,
        [
            "run",
            str(FIXTURES / "team.yaml"),
            "Should we buy Alphabet?",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0
    assert "completed" in result.output


# ---------------------------------------------------------------------------
# orchestra run — real provider (helpful error)
# ---------------------------------------------------------------------------


def test_run_with_real_provider_exits_nonzero(tmp_path: Path) -> None:
    cfg = make_real_provider_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code != 0


def test_run_with_real_provider_shows_helpful_message(tmp_path: Path) -> None:
    cfg = make_real_provider_team_config(tmp_path)
    result = runner.invoke(
        app,
        ["run", str(cfg), "Test question", "--output-dir", str(tmp_path / "out")],
    )
    assert "LiteLLM" in result.output or "provider" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI invoked with no arguments
# ---------------------------------------------------------------------------


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # Typer shows help when no_args_is_help=True
    assert result.exit_code == 0 or "Usage" in result.output
