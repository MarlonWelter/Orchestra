# ADR-006: Prompt Assembly

## Status

Accepted

## Context

Each agent turn requires a prompt assembled from three conceptually distinct parts:

1. A system-level protocol and team context
2. The active agent's role description
3. The current turn's task and conversation history

Before implementing the agent loop, the exact structure of the messages list and the responsibilities of the assembly component need to be defined.

## Decision

Introduce an explicit `PromptBuilder` component responsible for all prompt assembly. The agent loop calls `PromptBuilder` to produce a finished messages list. The `ModelClient` receives that list and has no knowledge of how it was constructed.

The v1 messages list has three entries:

```python
messages = [
    {
        "role": "system",
        "content": build_system_prompt(team),
    },
    {
        "role": "system",
        "content": load_role_description(active_agent),
    },
    {
        "role": "user",
        "content": build_current_input(
            sender=sender_agent,
            active_agent=active_agent,
            task=task,
            energy=energy,
            allowed_recipients=allowed_recipients,
            can_finalize=can_finalize,
            history=formatted_history,
        ),
    },
]
```

## Part 1: System prompt

The effective system prompt is assembled once per run from two layers:

**Global protocol prompt** — static across all teams and runs:

- A brief description of how Orchestra works
- The `AgentTurnResult` JSON schema with a concrete example
- The JSON-only output rule: the model must respond with a valid `AgentTurnResult` object and nothing else
- Energy behavior: what energy remaining means, and that agents should prefer moving toward a result when energy is low
- General handoff rules

**Team context** — static per run, derived from the team config:

- Team id and name
- Entry agent
- Team roster: agent names and one-line role descriptions only
- Allowed handoff routes if configured
- Finalization permissions

Example team roster section:

```text
Team roster:
- George: Manager and final synthesizer. Can finalize.
- Warren: Conservative value-investing analyst.
- Elon: Technology and disruption analyst.
- Klaus: Practical downside-risk reviewer.
```

Full role descriptions do not appear in the roster. The active agent receives its own full role description separately.

## Part 2: Role description

Loaded from the agent's configured `role_prompt` file. Contains:

- The agent's identity, character, and perspective
- Their area of expertise and responsibilities
- Their relationships to other agents
- Any constraints or beliefs that shape their responses

Rules:

- Role files must not repeat the handoff protocol. Protocol rules belong in the system prompt.
- Role files must not override engine routing rules. A role prompt may describe preferred routing behavior, for example "you usually return to George after analysis," but the engine config takes precedence. If the config says an agent may not hand off to a particular recipient, that constraint holds regardless of what the role prompt says.

## Part 3: Current input

Assembled fresh for each turn. Contains the active agent's context for this specific call.

Example:

```text
Active agent: Warren
From: George (Manager)
Task: Analyze Alphabet's current valuation from a conservative value investing perspective.
Energy remaining: 14 / 20
Allowed recipients: George, Elon, Klaus
Can finalize: false

--- Run history ---

[Turn 1] George → Warren
Task: Analyze Alphabet's current valuation...
Response: Alphabet is a high-quality business, but the current price leaves limited margin of safety under conservative assumptions.
Handoff: continue → Warren

[Turn 2] Warren → Elon
Task: Evaluate Alphabet's AI upside and platform optionality...
Response: ...
Handoff: continue → Klaus

---

Return only a valid AgentTurnResult JSON object.
```

Fields included:

| Field | Purpose |
|---|---|
| `Active agent` | Identifies the agent that must respond |
| `From` | Sender agent name and role |
| `Task` | The concrete task from the previous handoff |
| `Energy remaining` | Current / initial, so the agent can calibrate delegation |
| `Allowed recipients` | Valid handoff targets for this turn, derived from agent config |
| `Can finalize` | Whether this agent may return a `final` handoff |
| Run history | All prior turns as formatted transcript text |
| JSON reminder | Short output reminder at the end |

The `AgentTurnResult` schema is defined only in the system prompt. The current input ends with a short one-line reminder ("Return only a valid AgentTurnResult JSON object.") but does not repeat the full schema. This is low-cost repetition that improves output reliability.

## History format

Prior turns are included as **formatted transcript text** inside the current input, not as synthetic alternating `user`/`assistant` messages.

Reason: Orchestra run history is a multi-agent event log, not a two-party conversation. Representing it as alternating `user`/`assistant` messages risks the active model identifying with prior assistant messages written by other agents. Keeping history as structured transcript text avoids this ambiguity.

For v1, the full history of all prior turns is included. Filtering, compression, and relevance selection are deferred.

## PromptBuilder interface

```python
class PromptBuilder:
    def build_messages(
        self,
        team: TeamConfig,
        active_agent: AgentConfig,
        run: RunState,
        current_task: str,
        sender_agent_id: str | None,
        energy: EnergyState,
    ) -> list[dict]:
        ...
```

Internal methods:

```python
build_system_prompt(team: TeamConfig) -> str
load_role_description(agent: AgentConfig) -> str
build_current_input(...) -> str
format_history(turns: list[TurnRecord]) -> str
```

Each method is independently unit-testable without a model provider.

## What is stored in the transcript

The full assembled messages list is stored in the turn transcript alongside the logical input fields. This enables exact replay of any turn for debugging.

## Consequences

- The agent loop does not construct prompts directly. It calls `PromptBuilder`.
- The `ModelClient` receives a finished messages list and has no knowledge of prompt structure.
- Role prompt files are protocol-free and focused on agent identity.
- Engine routing config takes precedence over any routing preferences expressed in role prompts.
- Full run history is included in v1. Token cost will eventually require filtering; the transcript stores all data needed to implement that later.
- Prompt structure can be changed without touching the agent loop or the model client.
