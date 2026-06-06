# Config Format

This document sketches the initial Orchestra configuration format. It is intentionally small and file-based for v1.

## Goals

The config format should make it easy to define:

- a team;
- an entry agent;
- which agents exist;
- which model profile each agent uses;
- each agent's role prompt;
- which agents may finalize;
- the initial energy budget;
- and model profiles backed by LiteLLM.

## Example

```yaml
team:
  id: investment_team
  name: Investment Team
  entry_agent: george
  default_energy: 20

models:
  frontier:
    provider: litellm
    model: anthropic/claude-sonnet-4-6
    temperature: 0.2
    max_tokens: 4000

  cheap:
    provider: litellm
    model: openai/gpt-4o-mini
    temperature: 0.2
    max_tokens: 2000

  local:
    provider: litellm
    model: ollama/mistral
    temperature: 0.2
    max_tokens: 2000

agents:
  george:
    name: George
    role_prompt: agents/george.md
    model_profile: frontier
    can_finalize: true
    can_handoff_to:
      - warren
      - elon
      - klaus

  warren:
    name: Warren
    role_prompt: agents/warren.md
    model_profile: frontier
    can_finalize: false
    can_handoff_to:
      - george
      - elon
      - klaus

  elon:
    name: Elon
    role_prompt: agents/elon.md
    model_profile: frontier
    can_finalize: false
    can_handoff_to:
      - george
      - warren
      - klaus

  klaus:
    name: Klaus
    role_prompt: agents/klaus.md
    model_profile: cheap
    can_finalize: false
    can_handoff_to:
      - george
      - warren
      - elon
```

## Team section

```yaml
team:
  id: investment_team
  name: Investment Team
  entry_agent: george
  default_energy: 20
```

| Field | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable machine-readable team id |
| `name` | no | Human-readable team name |
| `entry_agent` | yes | Agent that receives external input first |
| `default_energy` | yes | Initial energy budget for a run unless overridden |

## Models section

Model profiles are named model configurations. Agents reference profiles instead of raw model strings.

```yaml
models:
  frontier:
    provider: litellm
    model: anthropic/claude-sonnet-4-6
    temperature: 0.2
    max_tokens: 4000
```

| Field | Required | Meaning |
|---|---:|---|
| `provider` | yes | Model access implementation. v1 supports `litellm`. |
| `model` | yes | LiteLLM model string |
| `temperature` | no | Default temperature for this profile |
| `max_tokens` | no | Default output-token limit |
| `timeout_seconds` | no | Optional model call timeout |

## Agents section

```yaml
agents:
  george:
    name: George
    role_prompt: agents/george.md
    model_profile: frontier
    can_finalize: true
    can_handoff_to:
      - warren
      - elon
      - klaus
```

| Field | Required | Meaning |
|---|---:|---|
| `name` | no | Human-readable display name |
| `role_prompt` | yes | Path to this agent's role description |
| `model_profile` | yes | Model profile used for this agent |
| `can_finalize` | no | Whether this agent may return final output to the outside caller. Default: `false`. |
| `can_handoff_to` | no | Allowed recipients. If omitted, all agents are allowed. |

## Role prompt files

Each agent's `role_prompt` points to a markdown file. The file contains only the role-specific description:

```markdown
# George

You are George, the manager of the team...
```

The global system prompt and current input are assembled by the engine at runtime. Role prompt files should not duplicate the generic handoff protocol.

## Energy

For v1:

- every model call costs `1` energy;
- invalid-output repair attempts also cost `1` energy;
- token usage is logged but does not affect the budget;
- when energy reaches `0`, the engine stops and returns an exhaustion summary.

The energy formula can become token- or cost-aware later.

## Validation

Before running a team, the engine should validate:

- `team.entry_agent` exists;
- every agent has a valid `role_prompt` file;
- every `model_profile` exists;
- every `can_handoff_to` recipient exists;
- at least one agent can finalize;
- `default_energy` is positive;
- model profiles have provider and model values.

## Deferred fields

The following are intentionally deferred:

- per-agent tools;
- per-agent memory folders;
- shared team memory;
- automatic scheduled inputs;
- fallback model profiles;
- cost limits;
- parallel execution;
- nested teams.

These should be added only after the first sequential loop works reliably.
