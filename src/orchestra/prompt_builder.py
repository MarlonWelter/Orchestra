"""
Prompt assembly for Orchestra agent turns.

Builds the three-part messages list that the engine passes to ModelClient.
All prompt construction lives here; ModelClient receives a finished list and
has no knowledge of how it was assembled.

Message shape (ADR-006):
    [
        {"role": "system", "content": <global protocol + team context>},
        {"role": "system", "content": <active agent role description>},
        {"role": "user",   "content": <current turn input>},
    ]

History is formatted as structured transcript text, not as synthetic
alternating user/assistant messages (ADR-006).
"""

from __future__ import annotations

from orchestra.config import AgentConfig, TeamConfig
from orchestra.errors import PromptAssemblyError
from orchestra.schemas import EnergyState, TurnRecord

# ---------------------------------------------------------------------------
# Schema embedded in the system prompt (ADR-008)
# ---------------------------------------------------------------------------

_AGENT_TURN_RESULT_SCHEMA = """\
{
  "agent_id": "<your agent id — must match exactly>",
  "response": "<your full response to the task>",
  "handoff": {
    "type": "<continue | return | final>",
    "recipient": "<agent_id, or 'external' for final>",
    "task": "<concrete task for the next agent, or null for final>"
  },
  "notes": ["<optional notes for the run transcript>"]
}\
"""

_HANDOFF_TYPES_DESCRIPTION = """\
Handoff types:
- "continue"  Delegate a sub-task forward to another agent.
- "return"    Your contribution is complete. Hand the result to the next agent.
- "final"     Return the final answer to the outside caller.
              recipient must be "external" and task must be null.\
"""

_JSON_REMINDER = "Return only a valid AgentTurnResult JSON object."


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


class PromptBuilder:
    """
    Assembles the messages list for one agent turn.

    The engine creates one PromptBuilder per run (or per call — it is
    stateless) and calls build_messages for every turn.
    """

    def build_messages(
        self,
        team: TeamConfig,
        active_agent: AgentConfig,
        turn_history: list[TurnRecord],
        current_task: str,
        sender_agent_id: str,
        energy: EnergyState,
    ) -> list[dict[str, str]]:
        """
        Build the three-message list for the active agent's turn.

        Args:
            team:             Loaded team configuration.
            active_agent:     The agent that will respond this turn.
            turn_history:     All completed TurnRecord objects so far.
            current_task:     The task string from the previous handoff
                              (or the external input for turn 1).
            sender_agent_id:  ID of the agent that sent the task.
                              Use "external" for the first turn.
            energy:           Current energy state.

        Returns:
            A list of three message dicts ready for ModelClient.

        Raises:
            PromptAssemblyError: if the role prompt file cannot be read.
        """
        system_prompt = self._build_system_prompt(team)
        role_description = self._load_role_description(active_agent)
        current_input = self._build_current_input(
            team=team,
            active_agent=active_agent,
            turn_history=turn_history,
            current_task=current_task,
            sender_agent_id=sender_agent_id,
            energy=energy,
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": role_description},
            {"role": "user", "content": current_input},
        ]

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_system_prompt(self, team: TeamConfig) -> str:
        """
        Build the global protocol prompt + team context.

        This is the same for every agent in the team and is assembled once
        per run (it does not depend on which agent is active).
        """
        roster_lines = []
        for agent in team.agents.values():
            finalizer = " Can finalize." if agent.can_finalize else ""
            roster_lines.append(f"- {agent.name} ({agent.id}){finalizer}")
        roster = "\n".join(roster_lines)

        return f"""\
Orchestra is a turn-based multi-agent orchestration engine. You are one agent \
in a team. Agents take turns: one agent receives a task, processes it from \
their role, and either delegates to another agent or returns the final answer.

## Response format

You must respond with a valid AgentTurnResult JSON object and nothing else.
Do not include any text before or after the JSON.

Schema:

{_AGENT_TURN_RESULT_SCHEMA}

{_HANDOFF_TYPES_DESCRIPTION}

## Energy

Each model call costs 1 energy unit. When energy remaining is 3 or fewer, \
prefer returning results over delegating further.

## Team

Name: {team.name}
Entry agent: {team.entry_agent}

Roster:
{roster}"""

    def _load_role_description(self, agent: AgentConfig) -> str:
        """
        Load the agent's role prompt file.

        Raises PromptAssemblyError if the file cannot be read.
        """
        try:
            return agent.role_prompt.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptAssemblyError(
                f"Could not read role prompt for agent '{agent.id}': "
                f"{agent.role_prompt} — {exc}"
            ) from exc

    def _build_current_input(
        self,
        *,
        team: TeamConfig,
        active_agent: AgentConfig,
        turn_history: list[TurnRecord],
        current_task: str,
        sender_agent_id: str,
        energy: EnergyState,
    ) -> str:
        """
        Build the current-turn user message.

        Contains: active agent, sender, task, energy, allowed recipients,
        finalization permission, run history, and the JSON reminder.
        """
        # Resolve allowed recipients
        if active_agent.can_handoff_to is None:
            allowed = [
                a for a in team.agents.values() if a.id != active_agent.id
            ]
        else:
            allowed = [
                team.agents[r]
                for r in active_agent.can_handoff_to
                if r in team.agents
            ]

        allowed_str = (
            ", ".join(f"{a.name} ({a.id})" for a in allowed)
            if allowed
            else "none"
        )

        can_finalize_str = "true" if active_agent.can_finalize else "false"

        # Resolve sender display name
        if sender_agent_id == "external":
            sender_str = "external"
        elif sender_agent_id in team.agents:
            sender_agent = team.agents[sender_agent_id]
            sender_str = f"{sender_agent.name} ({sender_agent_id})"
        else:
            sender_str = sender_agent_id

        history_str = self._format_history(turn_history)

        return (
            f"Active agent: {active_agent.name} ({active_agent.id})\n"
            f"From: {sender_str}\n"
            f"Task: {current_task}\n"
            f"Energy remaining: {energy.remaining} / {energy.initial}\n"
            f"Allowed recipients: {allowed_str}\n"
            f"Can finalize: {can_finalize_str}\n"
            f"\n"
            f"{history_str}"
            f"\n"
            f"{_JSON_REMINDER}"
        )

    def _format_history(self, turns: list[TurnRecord]) -> str:
        """
        Format completed turns as structured transcript text.

        History is plain text — not synthetic alternating user/assistant
        messages — to avoid the model identifying with prior agents (ADR-006).
        """
        if not turns:
            return "--- Run history ---\n\n(No prior turns)\n"

        lines = ["--- Run history ---", ""]
        for turn in turns:
            sender = turn.sender_agent_id
            agent = turn.agent_id
            task = turn.logical_input.get("task", "")
            response = turn.parsed_result.get("response", "")
            handoff = turn.parsed_result.get("handoff", {})
            handoff_type = handoff.get("type", "")
            recipient = handoff.get("recipient", "")

            lines.append(f"[Turn {turn.index}] {sender} → {agent}")
            if task:
                lines.append(f"Task: {task}")
            if response:
                lines.append(f"Response: {response}")
            if handoff_type:
                lines.append(f"Handoff: {handoff_type} → {recipient}")
            lines.append("")

        return "\n".join(lines)
