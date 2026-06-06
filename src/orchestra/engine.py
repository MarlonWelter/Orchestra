"""
Orchestra engine — the main run loop.

Implements the ADR-009 per-turn cycle:

  1.  Energy check     — exhaust immediately if remaining == 0
  2.  Bookkeeping      — assign turn_id, record energy_before
  3.  Energy deduction — 1 unit for the upcoming model call
  4.  Prompt assembly  — delegate to PromptBuilder
  5.  Model call       — delegate to ModelClient
  6.  Parse response   — JSON decode + Pydantic validation
  7.  Repair           — one retry if parse fails (costs 1 extra unit)
  8.  Handoff validate — validate_handoff() raises HandoffValidationError on violation
  9.  Write turn       — append TurnSummary to RunState; write TurnRecord to disk
  10. Route            — advance to next agent or terminate

Special rules:
  - "final" beats exhaustion: if energy hits 0 during a turn and the agent
    returns handoff.type == "final", the run status is "completed", not "exhausted".
  - Repair failure triggers escalation: the engine looks for a can_finalize
    agent (excluding the failing one) and routes there.  If none exists, the
    run status is "failed".
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from orchestra.config import AgentConfig, TeamConfig
from orchestra.errors import HandoffValidationError
from orchestra.model_client import ModelClient
from orchestra.prompt_builder import PromptBuilder
from orchestra.schemas import (
    AgentTurnResult,
    EnergyState,
    HandoffType,
    ModelRequest,
    ModelResponse,
    RepairAttempt,
    RunState,
    RunStatus,
    TurnRecord,
    TurnStatus,
    TurnSummary,
    ValidationResult,
)
from orchestra.transcript_store import TranscriptStore, generate_run_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handoff validation — public, tested independently
# ---------------------------------------------------------------------------


def validate_handoff(
    result: AgentTurnResult,
    active_agent: AgentConfig,
    team: TeamConfig,
) -> None:
    """
    Validate that the handoff carried by result is legal for active_agent.

    Raises:
        HandoffValidationError: on any rule violation.
    """
    handoff = result.handoff

    if handoff.type == HandoffType.final:
        if not active_agent.can_finalize:
            raise HandoffValidationError(
                f"Agent '{active_agent.id}' issued handoff type 'final' "
                f"but can_finalize=False.",
                agent_id=active_agent.id,
                handoff=handoff,
            )
        if handoff.recipient != "external":
            raise HandoffValidationError(
                f"Handoff type 'final' requires recipient='external', "
                f"got '{handoff.recipient}'.",
                agent_id=active_agent.id,
                handoff=handoff,
            )
        return  # task may be None for final — that is valid

    # continue / return — recipient must be a known, different agent
    recipient_id = handoff.recipient

    if recipient_id not in team.agents:
        raise HandoffValidationError(
            f"Agent '{active_agent.id}' handed off to unknown agent "
            f"'{recipient_id}'.",
            agent_id=active_agent.id,
            handoff=handoff,
        )

    if recipient_id == active_agent.id:
        raise HandoffValidationError(
            f"Agent '{active_agent.id}' attempted to hand off to itself.",
            agent_id=active_agent.id,
            handoff=handoff,
        )

    if active_agent.can_handoff_to is not None:
        if recipient_id not in active_agent.can_handoff_to:
            raise HandoffValidationError(
                f"Agent '{active_agent.id}' is not allowed to hand off to "
                f"'{recipient_id}'. Allowed: {active_agent.can_handoff_to}.",
                agent_id=active_agent.id,
                handoff=handoff,
            )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    """
    Executes a multi-agent run end-to-end.

    The engine is stateless between runs.  All mutable state lives in
    RunState, which is created inside run() and returned on completion.

    All dependencies are injected at construction time so they can be
    swapped for test doubles without subclassing.
    """

    def __init__(
        self,
        model_client: ModelClient,
        *,
        prompt_builder: PromptBuilder | None = None,
        transcript_store: TranscriptStore | None = None,
    ) -> None:
        self._client = model_client
        self._builder = prompt_builder or PromptBuilder()
        self._store = transcript_store or TranscriptStore()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        team: TeamConfig,
        external_input: str,
        *,
        run_id: str | None = None,
    ) -> RunState:
        """
        Execute a full run and return the final RunState.

        Args:
            team:           Loaded team configuration.
            external_input: The task supplied by the outside caller.
            run_id:         Optional ID override; auto-generated if omitted.

        Returns:
            RunState with status in {completed, failed, exhausted}.
        """
        effective_run_id = run_id or generate_run_id()

        run_state = RunState(
            run_id=effective_run_id,
            team_id=team.id,
            status=RunStatus.running,
            external_input=external_input,
            entry_agent=team.entry_agent,
            started_at=_now_iso(),
            energy=EnergyState(
                initial=team.default_energy,
                used=0,
                remaining=team.default_energy,
            ),
        )
        self._store.start_run(run_state)

        current_agent_id: str = team.entry_agent
        current_task: str = external_input
        sender_id: str = "external"
        turn_history: list[TurnRecord] = []
        turn_index: int = 0

        while True:
            active_agent = team.agents[current_agent_id]
            energy_before = run_state.energy.remaining

            # ── Step 1: energy check ───────────────────────────────────
            if run_state.energy.remaining == 0:
                self._store.exhaust_run(run_state)
                return run_state

            # ── Step 2: bookkeeping ────────────────────────────────────
            turn_index += 1
            turn_id = f"{turn_index:03d}-{current_agent_id}"
            started_at = _now_iso()

            # ── Step 3: deduct energy for the upcoming model call ──────
            _deduct_energy(run_state, 1)

            # ── Step 4: build prompt ───────────────────────────────────
            messages = self._builder.build_messages(
                team=team,
                active_agent=active_agent,
                turn_history=turn_history,
                current_task=current_task,
                sender_agent_id=sender_id,
                energy=run_state.energy,
            )

            # ── Step 5: call model ─────────────────────────────────────
            request = ModelRequest(
                run_id=effective_run_id,
                turn_id=turn_id,
                agent_id=current_agent_id,
                model_profile=active_agent.model_profile,
                messages=messages,
            )
            try:
                response = self._client.complete(request)
            except Exception as exc:
                logger.error(
                    "Model call failed for agent '%s' (turn %s): %s",
                    current_agent_id,
                    turn_id,
                    exc,
                )
                failed_record = TurnRecord(
                    turn_id=turn_id,
                    run_id=effective_run_id,
                    index=turn_index,
                    agent_id=current_agent_id,
                    sender_agent_id=sender_id,
                    status=TurnStatus.failed,
                    started_at=started_at,
                    completed_at=_now_iso(),
                    energy_before=energy_before,
                    energy_cost=energy_before - run_state.energy.remaining,
                    energy_after=run_state.energy.remaining,
                    logical_input={"task": current_task, "sender": sender_id},
                    error=str(exc),
                )
                self._finish_failed_turn(run_state, failed_record)
                self._store.fail_run(run_state, f"Model provider error: {exc}")
                return run_state

            # ── Step 6: parse response ─────────────────────────────────
            result, raw_text, parse_error = _parse_response(response)
            repair_attempts: list[RepairAttempt] = []

            if result is None:
                # ── Step 7: repair ─────────────────────────────────────
                attempt = RepairAttempt(
                    raw_content=raw_text,
                    validation_error=parse_error,
                )

                if run_state.energy.remaining > 0:
                    _deduct_energy(run_state, 1)
                    repair_messages = _build_repair_messages(
                        messages, raw_text, parse_error
                    )
                    repair_request = ModelRequest(
                        run_id=effective_run_id,
                        turn_id=f"{turn_id}_repair",
                        agent_id=current_agent_id,
                        model_profile=active_agent.model_profile,
                        messages=repair_messages,
                    )
                    try:
                        repair_response = self._client.complete(repair_request)
                        result, repair_raw, _ = _parse_response(repair_response)
                        attempt.repair_raw_content = repair_raw
                        attempt.repair_valid = result is not None
                    except Exception as repair_exc:
                        logger.warning(
                            "Repair call failed for agent '%s': %s",
                            current_agent_id,
                            repair_exc,
                        )
                        attempt.repair_valid = False
                else:
                    # No energy left for repair
                    attempt.repair_valid = False

                repair_attempts.append(attempt)

                if result is None:
                    # Repair exhausted — write failed turn and try escalation
                    failed_record = TurnRecord(
                        turn_id=turn_id,
                        run_id=effective_run_id,
                        index=turn_index,
                        agent_id=current_agent_id,
                        sender_agent_id=sender_id,
                        status=TurnStatus.failed,
                        started_at=started_at,
                        completed_at=_now_iso(),
                        energy_before=energy_before,
                        energy_cost=energy_before - run_state.energy.remaining,
                        energy_after=run_state.energy.remaining,
                        logical_input={"task": current_task, "sender": sender_id},
                        assembled_input={"messages": messages},
                        repair_attempts=repair_attempts,
                        validation=ValidationResult(
                            valid=False, errors=[parse_error]
                        ),
                        error=f"Parse failed after repair: {parse_error}",
                    )
                    self._finish_failed_turn(run_state, failed_record)

                    escalation = _try_escalate(
                        current_agent_id=current_agent_id,
                        current_task=current_task,
                        team=team,
                    )
                    if escalation:
                        current_agent_id, sender_id, current_task = escalation
                        continue  # re-enter loop with escalated agent

                    self._store.fail_run(
                        run_state,
                        f"Agent '{current_agent_id}' could not produce valid output "
                        f"after repair, and no escalation target is available.",
                    )
                    return run_state

            # ── Step 8: validate handoff ───────────────────────────────
            assert result is not None  # narrowed by the repair block above
            try:
                validate_handoff(result, active_agent, team)
            except HandoffValidationError as exc:
                logger.error(
                    "Invalid handoff from agent '%s': %s", current_agent_id, exc
                )
                failed_record = TurnRecord(
                    turn_id=turn_id,
                    run_id=effective_run_id,
                    index=turn_index,
                    agent_id=current_agent_id,
                    sender_agent_id=sender_id,
                    status=TurnStatus.failed,
                    started_at=started_at,
                    completed_at=_now_iso(),
                    energy_before=energy_before,
                    energy_cost=energy_before - run_state.energy.remaining,
                    energy_after=run_state.energy.remaining,
                    logical_input={"task": current_task, "sender": sender_id},
                    assembled_input={"messages": messages},
                    model_response=response.model_dump(),
                    parsed_result=result.model_dump(),
                    repair_attempts=repair_attempts,
                    validation=ValidationResult(valid=False, errors=[str(exc)]),
                    error=str(exc),
                )
                self._finish_failed_turn(run_state, failed_record)
                self._store.fail_run(run_state, str(exc))
                return run_state

            # ── Step 9: write completed turn ───────────────────────────
            handoff = result.handoff
            completed_at = _now_iso()
            energy_cost = energy_before - run_state.energy.remaining

            turn_record = TurnRecord(
                turn_id=turn_id,
                run_id=effective_run_id,
                index=turn_index,
                agent_id=current_agent_id,
                sender_agent_id=sender_id,
                status=TurnStatus.completed,
                started_at=started_at,
                completed_at=completed_at,
                energy_before=energy_before,
                energy_cost=energy_cost,
                energy_after=run_state.energy.remaining,
                logical_input={"task": current_task, "sender": sender_id},
                assembled_input={"messages": messages},
                model_response=response.model_dump(),
                parsed_result=result.model_dump(),
                validation=ValidationResult(valid=True),
                repair_attempts=repair_attempts,
            )
            turn_summary = TurnSummary(
                turn_id=turn_id,
                index=turn_index,
                agent_id=current_agent_id,
                status=TurnStatus.completed,
                handoff_type=handoff.type.value,
                recipient=handoff.recipient,
                path=f"turns/{turn_id}.json",
            )

            run_state.turns.append(turn_summary)
            self._store.write_turn(turn_record)
            self._store.update_run(run_state)
            turn_history.append(turn_record)

            # ── Step 10: route ─────────────────────────────────────────
            if handoff.type == HandoffType.final:
                self._store.complete_run(run_state, result.response)
                return run_state

            # continue / return — advance to next agent
            sender_id = current_agent_id
            current_agent_id = handoff.recipient
            current_task = handoff.task or current_task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finish_failed_turn(
        self,
        run_state: RunState,
        turn_record: TurnRecord,
    ) -> None:
        """
        Record a failed turn in the run index and write it to disk.

        Appends a TurnSummary to run_state.turns so the index stays
        accurate even for failed turns.  Transcript write errors are logged
        but never re-raised to avoid masking the original failure reason.
        """
        summary = TurnSummary(
            turn_id=turn_record.turn_id,
            index=turn_record.index,
            agent_id=turn_record.agent_id,
            status=TurnStatus.failed,
            handoff_type=None,
            recipient=None,
            path=f"turns/{turn_record.turn_id}.json",
        )
        run_state.turns.append(summary)
        try:
            self._store.write_turn(turn_record)
            self._store.update_run(run_state)
        except Exception:
            logger.exception(
                "Failed to write failed turn record for turn '%s'",
                turn_record.turn_id,
            )


# ---------------------------------------------------------------------------
# Module-level helpers (internal)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deduct_energy(run_state: RunState, calls: int) -> None:
    """
    Deduct `calls` units from the run's energy budget in-place.

    Modifies run_state.energy.used and run_state.energy.remaining.
    Does not enforce a floor at zero — the caller checks energy before deducting.
    """
    run_state.energy.used += calls
    run_state.energy.remaining -= calls


def _parse_response(
    response: ModelResponse,
) -> tuple[AgentTurnResult | None, str, str]:
    """
    Extract an AgentTurnResult from a ModelResponse.

    Returns:
        (result_or_None, raw_text, error_description)

    raw_text is the original response text (empty string when structured_output
    was already available).  error_description is empty on success.
    """
    # Fast path: client already parsed the structured output
    if response.structured_output is not None:
        return response.structured_output, "", ""

    raw_text = (response.text or "").strip()
    if not raw_text:
        return None, raw_text, "Model returned an empty response."

    # Strip markdown code fences if the model wrapped the JSON
    text = _extract_json_text(raw_text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, raw_text, f"JSON decode error: {exc}"

    try:
        result = AgentTurnResult.model_validate(data)
    except Exception as exc:
        return None, raw_text, f"Schema validation error: {exc}"

    return result, raw_text, ""


def _extract_json_text(text: str) -> str:
    """
    Return the JSON portion of text, stripping markdown code fences if present.

    Handles ` ```json ... ``` ` and plain ` ``` ... ``` ` wrappers.
    Returns the original text unchanged if no fence is detected.
    """
    stripped = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", stripped, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return stripped


def _build_repair_messages(
    original_messages: list[dict[str, str]],
    raw_content: str,
    validation_error: str,
) -> list[dict[str, str]]:
    """
    Construct the message list for a repair attempt.

    Appends the model's bad response (as an assistant message) and a
    correction request (as a user message) to the original conversation.
    """
    correction = (
        "Your previous response was not a valid AgentTurnResult JSON object.\n\n"
        f"Error: {validation_error}\n\n"
        "Please respond with ONLY a valid AgentTurnResult JSON object. "
        "No preamble, no markdown code fences, no trailing text."
    )
    return [
        *original_messages,
        {"role": "assistant", "content": raw_content},
        {"role": "user", "content": correction},
    ]


def _try_escalate(
    *,
    current_agent_id: str,
    current_task: str,
    team: TeamConfig,
) -> tuple[str, str, str] | None:
    """
    Find a can_finalize agent (other than the failing one) for escalation.

    Iterates agents in config order and returns the first eligible one.

    Returns:
        (new_agent_id, sender_id, escalation_task) or None.
    """
    for agent in team.agents.values():
        if agent.id != current_agent_id and agent.can_finalize:
            escalation_task = (
                f"[Escalation] Agent '{current_agent_id}' failed to produce a valid "
                f"response. Please finalize the run based on available context. "
                f"Original task: {current_task}"
            )
            return agent.id, "engine", escalation_task
    return None
