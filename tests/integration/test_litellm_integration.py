"""
End-to-end integration tests for LiteLLMClient + Engine against real models.

All tests in this file are skipped automatically unless the required
API key environment variable is set.  They are NOT run in CI by default.

To run manually:
    OPENAI_API_KEY=sk-... uv run pytest tests/integration/test_litellm_integration.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not OPENAI_KEY,
    reason="OPENAI_API_KEY not set — skipping live LiteLLM integration tests",
)


@pytest.fixture()
def openai_team(tmp_path: Path):
    """Load a minimal one-agent team configured for OpenAI gpt-4o-mini."""
    import yaml
    from orchestra.config import load_config

    role_dir = tmp_path / "agents"
    role_dir.mkdir()
    (role_dir / "alice.md").write_text(
        "# Alice\n\nYou are Alice, a concise AI assistant. "
        "Always respond with a final handoff and a one-sentence answer.",
        encoding="utf-8",
    )

    config = {
        "team": {"id": "t", "entry_agent": "alice", "default_energy": 5},
        "models": {
            "cheap": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "max_tokens": 500,
                "temperature": 0.0,
            }
        },
        "agents": {
            "alice": {
                "name": "Alice",
                "role_prompt": "agents/alice.md",
                "model_profile": "cheap",
                "can_finalize": True,
            }
        },
    }
    cfg_path = tmp_path / "team.yaml"
    cfg_path.write_text(yaml.dump(config), encoding="utf-8")
    return load_config(cfg_path)


def test_litellm_client_completes_one_turn(openai_team, tmp_path: Path) -> None:
    """Single-turn run against gpt-4o-mini should return a valid final answer."""
    from orchestra.engine import Engine
    from orchestra.litellm_client import LiteLLMClient
    from orchestra.schemas import RunStatus
    from orchestra.transcript_store import TranscriptStore

    client = LiteLLMClient(openai_team.models)
    engine = Engine(client, transcript_store=TranscriptStore(tmp_path))
    run = engine.run(openai_team, "What is 2 + 2?", run_id="run_live_test")

    assert run.status == RunStatus.completed, f"Run failed: {run.error}"
    assert run.final_answer is not None
    assert len(run.final_answer) > 0


def test_litellm_client_response_has_usage(openai_team) -> None:
    """The ModelResponse returned by LiteLLMClient should include token usage."""
    from orchestra.litellm_client import LiteLLMClient
    from orchestra.schemas import ModelRequest

    client = LiteLLMClient(openai_team.models)
    request = ModelRequest(
        run_id="run_usage_test",
        turn_id="001-alice",
        agent_id="alice",
        model_profile="cheap",
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant. Respond in valid AgentTurnResult JSON.",
            },
            {
                "role": "user",
                "content": (
                    'Respond with: {"agent_id":"alice","response":"Hi.","handoff":'
                    '{"type":"final","recipient":"external","task":null}}'
                ),
            },
        ],
    )
    response = client.complete(request)

    assert response.usage.input_tokens is not None
    assert response.usage.input_tokens > 0
