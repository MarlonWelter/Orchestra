"""
Team configuration loading and validation.

Loads team.yaml, resolves all paths relative to the config file location,
and validates the team structure before any run starts. Raises ConfigError
on any violation so the engine never starts with a broken config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from orchestra.errors import ConfigError


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ModelProfileConfig:
    provider: str
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None


@dataclass
class AgentConfig:
    id: str
    name: str
    role_prompt: Path
    model_profile: str
    can_finalize: bool = False
    can_handoff_to: list[str] | None = None  # None = all agents allowed


@dataclass
class TeamConfig:
    id: str
    name: str
    entry_agent: str
    default_energy: int
    models: dict[str, ModelProfileConfig]
    agents: dict[str, AgentConfig]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Path) -> TeamConfig:
    """
    Load and validate a team.yaml file.

    All paths in the config are resolved relative to the directory that
    contains the config file, not the current working directory.

    Raises ConfigError on any validation failure.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config file: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping")

    base_dir = path.parent

    team_raw = raw.get("team", {})
    models_raw = raw.get("models", {})
    agents_raw = raw.get("agents", {})

    # --- Team section ---
    team_id = _require_str(team_raw, "team.id")
    team_name = team_raw.get("name", team_id)
    entry_agent = _require_str(team_raw, "team.entry_agent")
    default_energy = team_raw.get("default_energy")

    if default_energy is None:
        raise ConfigError("team.default_energy is required")
    if not isinstance(default_energy, int) or default_energy <= 0:
        raise ConfigError("team.default_energy must be a positive integer")

    # --- Model profiles ---
    if not isinstance(models_raw, dict) or not models_raw:
        raise ConfigError("models section is required and must not be empty")

    models: dict[str, ModelProfileConfig] = {}
    for profile_name, profile_raw in models_raw.items():
        if not isinstance(profile_raw, dict):
            raise ConfigError(f"models.{profile_name} must be a mapping")
        provider = _require_str(profile_raw, f"models.{profile_name}.provider")
        model = _require_str(profile_raw, f"models.{profile_name}.model")
        models[profile_name] = ModelProfileConfig(
            provider=provider,
            model=model,
            temperature=profile_raw.get("temperature"),
            max_tokens=profile_raw.get("max_tokens"),
            timeout_seconds=profile_raw.get("timeout_seconds"),
        )

    # --- Agents ---
    if not isinstance(agents_raw, dict) or not agents_raw:
        raise ConfigError("agents section is required and must not be empty")

    agents: dict[str, AgentConfig] = {}
    for agent_id, agent_raw in agents_raw.items():
        if not isinstance(agent_raw, dict):
            raise ConfigError(f"agents.{agent_id} must be a mapping")

        agent_name = agent_raw.get("name", agent_id)
        role_prompt_str = _require_str(agent_raw, f"agents.{agent_id}.role_prompt")
        role_prompt = (base_dir / role_prompt_str).resolve()
        model_profile = _require_str(agent_raw, f"agents.{agent_id}.model_profile")
        can_finalize = bool(agent_raw.get("can_finalize", False))
        can_handoff_to_raw = agent_raw.get("can_handoff_to")
        can_handoff_to: list[str] | None = None
        if can_handoff_to_raw is not None:
            if not isinstance(can_handoff_to_raw, list):
                raise ConfigError(
                    f"agents.{agent_id}.can_handoff_to must be a list"
                )
            can_handoff_to = [str(r) for r in can_handoff_to_raw]

        agents[agent_id] = AgentConfig(
            id=agent_id,
            name=agent_name,
            role_prompt=role_prompt,
            model_profile=model_profile,
            can_finalize=can_finalize,
            can_handoff_to=can_handoff_to,
        )

    # --- Cross-reference validation ---
    _validate(
        team_id=team_id,
        entry_agent=entry_agent,
        models=models,
        agents=agents,
    )

    return TeamConfig(
        id=team_id,
        name=team_name,
        entry_agent=entry_agent,
        default_energy=default_energy,
        models=models,
        agents=agents,
    )


def _validate(
    *,
    team_id: str,
    entry_agent: str,
    models: dict[str, ModelProfileConfig],
    agents: dict[str, AgentConfig],
) -> None:
    # Entry agent must exist
    if entry_agent not in agents:
        raise ConfigError(
            f"team.entry_agent '{entry_agent}' does not exist in agents"
        )

    # At least one agent must be able to finalize
    if not any(a.can_finalize for a in agents.values()):
        raise ConfigError("At least one agent must have can_finalize: true")

    for agent in agents.values():
        # Model profile must exist
        if agent.model_profile not in models:
            raise ConfigError(
                f"agents.{agent.id}.model_profile '{agent.model_profile}' "
                f"does not exist in models"
            )

        # Role prompt file must exist
        if not agent.role_prompt.exists():
            raise ConfigError(
                f"agents.{agent.id}.role_prompt not found: {agent.role_prompt}"
            )

        # can_handoff_to recipients must exist
        if agent.can_handoff_to is not None:
            for recipient in agent.can_handoff_to:
                if recipient not in agents:
                    raise ConfigError(
                        f"agents.{agent.id}.can_handoff_to contains unknown "
                        f"agent '{recipient}'"
                    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_str(mapping: dict, key: str) -> str:
    """Return mapping[last segment of key] as a str, or raise ConfigError."""
    field = key.split(".")[-1]
    value = mapping.get(field)
    if value is None:
        raise ConfigError(f"{key} is required")
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value
