"""
Shared data contracts for Orchestra.

All types used across more than one module live here to prevent circular
imports. Use Pydantic models for anything that is serialized to disk or
passed across component boundaries.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class HandoffType(str, Enum):
    continue_ = "continue"
    return_ = "return"
    final = "final"


class RunStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    exhausted = "exhausted"


class TurnStatus(str, Enum):
    completed = "completed"
    failed = "failed"


# ---------------------------------------------------------------------------
# Handoff and agent turn result (ADR-003)
# ---------------------------------------------------------------------------


class Handoff(BaseModel):
    type: HandoffType
    recipient: str
    task: str | None = None


class AgentTurnResult(BaseModel):
    agent_id: str
    response: str
    handoff: Handoff
    notes: list[str] = []

    @field_validator("response")
    @classmethod
    def response_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("response must not be empty")
        return v


# ---------------------------------------------------------------------------
# Energy (ADR-001, ADR-009)
# ---------------------------------------------------------------------------


class EnergyState(BaseModel):
    initial: int
    used: int
    remaining: int


# ---------------------------------------------------------------------------
# Model client contracts (ADR-004)
# ---------------------------------------------------------------------------


class ModelUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ModelRequest(BaseModel):
    run_id: str
    turn_id: str
    agent_id: str
    model_profile: str
    messages: list[dict[str, Any]]
    response_schema: Any | None = None
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None


class ModelResponse(BaseModel):
    text: str | None = None
    structured_output: AgentTurnResult | None = None
    tool_calls: list[dict[str, Any]] = []
    usage: ModelUsage = ModelUsage()
    model: str = ""
    provider: str | None = None
    raw_response: Any | None = None
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# Repair and validation (for transcript, ADR-005)
# ---------------------------------------------------------------------------


class RepairAttempt(BaseModel):
    raw_content: str
    validation_error: str
    repair_raw_content: str | None = None
    repair_valid: bool = False


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []


# ---------------------------------------------------------------------------
# Turn record (ADR-005)
# ---------------------------------------------------------------------------


class TurnSummary(BaseModel):
    """Lightweight entry stored in run.json's turn index."""

    turn_id: str
    index: int
    agent_id: str
    status: TurnStatus
    handoff_type: str | None = None
    recipient: str | None = None
    path: str = ""


class TurnRecord(BaseModel):
    """Full turn detail written to the individual turn file."""

    turn_id: str
    run_id: str
    index: int
    agent_id: str
    sender_agent_id: str
    status: TurnStatus
    started_at: str = ""
    completed_at: str = ""
    energy_before: int = 0
    energy_cost: int = 0
    energy_after: int = 0
    logical_input: dict[str, Any] = {}
    assembled_input: dict[str, Any] = {}
    model_response: dict[str, Any] = {}
    parsed_result: dict[str, Any] = {}
    validation: ValidationResult = ValidationResult(valid=True)
    repair_attempts: list[RepairAttempt] = []
    error: str | None = None


# ---------------------------------------------------------------------------
# Run state (ADR-005)
# ---------------------------------------------------------------------------


class RunState(BaseModel):
    run_id: str
    team_id: str
    status: RunStatus = RunStatus.running
    external_input: str = ""
    entry_agent: str = ""
    started_at: str = ""
    completed_at: str | None = None
    energy: EnergyState = EnergyState(initial=0, used=0, remaining=0)
    turns: list[TurnSummary] = []
    final_answer: str | None = None
    error: str | None = None
