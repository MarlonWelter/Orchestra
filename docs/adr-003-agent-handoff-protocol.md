# ADR-003: Agent Handoff Protocol

## Status

Accepted

## Context

Orchestra is built around turn-based execution. At any point exactly one agent is active. After processing its input, the active agent must either hand control to another agent or finish the run.

This requires a machine-readable protocol. Free-form text alone is not reliable enough for the engine to decide what to do next.

## Decision

Every agent response must contain a structured `AgentTurnResult` object.

The result contains:

- the active agent id;
- the agent's response text;
- a handoff decision;
- optional notes for the run transcript;
- optional metadata for debugging and observability.

## Result shape

```json
{
  "agent_id": "warren",
  "response": "Alphabet is a high-quality business, but the current price leaves limited margin of safety under conservative assumptions.",
  "handoff": {
    "type": "continue",
    "recipient": "elon",
    "task": "Evaluate Alphabet's AI upside, platform optionality, and risk of disruptive technology shifts."
  },
  "notes": [
    "Assumption: no major regulatory breakup within the next five years."
  ]
}
```

## Handoff types

### `continue`

The active agent hands the task to another agent.

```json
{
  "type": "continue",
  "recipient": "klaus",
  "task": "Review the emotional and downside risk of this investment thesis."
}
```

Rules:

- `recipient` must reference an existing agent in the current team.
- `task` must be concrete enough for the next agent to act on.
- The engine decreases the energy budget and starts the next turn.

### `return`

The active agent returns the task to the agent that called it.

```json
{
  "type": "return",
  "recipient": "george",
  "task": "Use this assessment in the final synthesis."
}
```

Rules:

- `recipient` must be a previous sender in the run history.
- This is mainly used by specialist agents returning to a manager or coordinator.

### `final`

The active agent ends the run and returns the final answer to the outside caller.

```json
{
  "type": "final",
  "recipient": "external",
  "task": null
}
```

Rules:

- Only agents explicitly allowed to finalize may use `final`.
- By default, only the configured manager or entry agent may finalize.
- The engine stops after a valid final handoff.

## Validation rules

The engine must validate every agent result before continuing.

Validation includes:

- result is parseable;
- `agent_id` matches the active agent;
- `response` is non-empty;
- handoff type is known;
- recipient exists or is `external` for final output;
- recipient is allowed by the team's routing rules, if routing rules are configured;
- the run still has remaining energy;
- only permitted agents can finalize.

## Invalid output policy

If the model returns invalid output:

1. The engine performs one repair attempt by asking the same model to reformat the previous answer into valid `AgentTurnResult` JSON.
2. If repair fails, the turn is marked as failed.
3. The engine returns control to the manager if possible.
4. If the manager cannot be reached or energy is exhausted, the run ends with an error summary.

Invalid-output repair consumes no additional conceptual agent turn, but it does consume a model call and is logged.

## Conversation semantics

Only one agent has the ball at a time. Agents may still argue, critique, or discuss indirectly because every agent sees the relevant run history, but execution is sequential.

The handoff protocol is Orchestra's core abstraction. Model providers, tools, memory, and storage are replaceable; the turn result protocol should remain stable.
