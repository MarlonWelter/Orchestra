"""
Tests for config.py — loading and validation of team.yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orchestra.config import AgentConfig, ModelProfileConfig, TeamConfig, load_config
from orchestra.errors import ConfigError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent.parent / "fixtures" / "investment_team"


def write_config(tmp_path: Path, data: dict) -> Path:
    """Write a team.yaml to tmp_path and return its path."""
    config_path = tmp_path / "team.yaml"
    config_path.write_text(yaml.dump(data), encoding="utf-8")
    return config_path


def make_agent_file(tmp_path: Path, name: str) -> str:
    """Create a minimal role prompt file and return the relative path."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    return f"agents/{name}.md"


def minimal_config(tmp_path: Path) -> dict:
    """Return a valid minimal config dict with role prompt files created."""
    george_path = make_agent_file(tmp_path, "george")
    warren_path = make_agent_file(tmp_path, "warren")
    return {
        "team": {
            "id": "test_team",
            "name": "Test Team",
            "entry_agent": "george",
            "default_energy": 10,
        },
        "models": {
            "fake": {"provider": "fake", "model": "fake/default"},
        },
        "agents": {
            "george": {
                "name": "George",
                "role_prompt": george_path,
                "model_profile": "fake",
                "can_finalize": True,
                "can_handoff_to": ["warren"],
            },
            "warren": {
                "name": "Warren",
                "role_prompt": warren_path,
                "model_profile": "fake",
                "can_finalize": False,
                "can_handoff_to": ["george"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_config_loads(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, minimal_config(tmp_path))
    team = load_config(config_path)

    assert isinstance(team, TeamConfig)
    assert team.id == "test_team"
    assert team.entry_agent == "george"
    assert team.default_energy == 10
    assert "george" in team.agents
    assert "warren" in team.agents
    assert "fake" in team.models


def test_fixture_config_validates() -> None:
    team = load_config(FIXTURES / "team.yaml")
    assert team.id == "investment_team"
    assert team.entry_agent == "george"
    assert team.agents["george"].can_finalize is True
    assert team.agents["warren"].can_finalize is False


def test_model_profile_fields(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["models"]["frontier"] = {
        "provider": "litellm",
        "model": "anthropic/claude-sonnet-4-6",
        "temperature": 0.2,
        "max_tokens": 4000,
        "timeout_seconds": 30,
    }
    george_path = make_agent_file(tmp_path, "george2")
    cfg["agents"]["george2"] = {
        "role_prompt": george_path,
        "model_profile": "frontier",
        "can_finalize": False,
    }
    config_path = write_config(tmp_path, cfg)
    team = load_config(config_path)
    profile = team.models["frontier"]
    assert isinstance(profile, ModelProfileConfig)
    assert profile.temperature == 0.2
    assert profile.max_tokens == 4000
    assert profile.timeout_seconds == 30


def test_agent_without_can_handoff_to_allows_all(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"].pop("can_handoff_to")
    config_path = write_config(tmp_path, cfg)
    team = load_config(config_path)
    assert team.agents["george"].can_handoff_to is None


# ---------------------------------------------------------------------------
# ConfigError cases
# ---------------------------------------------------------------------------


def test_missing_entry_agent_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["team"]["entry_agent"] = "nobody"
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="entry_agent"):
        load_config(config_path)


def test_missing_model_profile_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["model_profile"] = "nonexistent"
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="model_profile"):
        load_config(config_path)


def test_missing_role_prompt_file_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["role_prompt"] = "agents/missing.md"
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="role_prompt"):
        load_config(config_path)


def test_invalid_handoff_recipient_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["can_handoff_to"] = ["warren", "nobody"]
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="nobody"):
        load_config(config_path)


def test_no_finalizing_agent_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["can_finalize"] = False
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="can_finalize"):
        load_config(config_path)


def test_default_energy_zero_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["team"]["default_energy"] = 0
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="default_energy"):
        load_config(config_path)


def test_default_energy_negative_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["team"]["default_energy"] = -5
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="default_energy"):
        load_config(config_path)


def test_config_file_not_found_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.yaml")


def test_missing_entry_agent_key_fails(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    del cfg["team"]["entry_agent"]
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="entry_agent"):
        load_config(config_path)


# ---------------------------------------------------------------------------
# Strict type validation
# ---------------------------------------------------------------------------


def test_can_finalize_string_false_rejected(tmp_path: Path) -> None:
    """YAML string 'false' must not be silently coerced to True."""
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["can_finalize"] = "false"
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="can_finalize"):
        load_config(config_path)


def test_can_finalize_string_true_rejected(tmp_path: Path) -> None:
    """YAML string 'true' must not be accepted as a boolean."""
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["can_finalize"] = "true"
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="can_finalize"):
        load_config(config_path)


def test_can_handoff_to_integer_items_rejected(tmp_path: Path) -> None:
    """Non-string items in can_handoff_to must be rejected."""
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["can_handoff_to"] = [123]
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="can_handoff_to"):
        load_config(config_path)


def test_can_handoff_to_empty_string_rejected(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["agents"]["george"]["can_handoff_to"] = [""]
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="can_handoff_to"):
        load_config(config_path)


def test_invalid_temperature_rejected(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["models"]["fake"]["temperature"] = "warm"
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="temperature"):
        load_config(config_path)


def test_invalid_max_tokens_rejected(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["models"]["fake"]["max_tokens"] = -100
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="max_tokens"):
        load_config(config_path)


def test_invalid_timeout_seconds_rejected(tmp_path: Path) -> None:
    cfg = minimal_config(tmp_path)
    cfg["models"]["fake"]["timeout_seconds"] = 0
    config_path = write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config(config_path)
