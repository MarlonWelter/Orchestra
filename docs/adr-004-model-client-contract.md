# ADR-004: Model Client Contract

## Status

Accepted

## Context

ADR-001 chooses LiteLLM as the v1 model access layer and establishes that Orchestra should wrap it behind a thin internal model client. This ADR defines that boundary.

The model client should be small. It is not intended to hide all LLM differences forever. Its purpose is to prevent provider access, logging, retries, usage tracking, and structured-output handling from leaking into the agent loop.

## Decision

Introduce an internal `ModelClient` abstraction with a single main operation:

```python
response = model_client.complete(request)
```

The agent loop talks only to this internal client. The first implementation uses LiteLLM.

## ModelRequest

A `ModelRequest` represents one model call for one active agent turn.

```python
@dataclass
class ModelRequest:
    run_id: str
    turn_id: str
    agent_id: str
    model_profile: str
    messages: list[dict]
    response_schema: type | None = None
    tools: list[dict] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
```

Required fields:

| Field | Purpose |
|---|---|
| `run_id` | Identifies the full Orchestra run |
| `turn_id` | Identifies the current agent turn |
| `agent_id` | Identifies the active agent |
| `model_profile` | Refers to a configured model profile |
| `messages` | Provider-neutral message list sent to the model |

Optional fields:

| Field | Purpose |
|---|---|
| `response_schema` | Expected structured output schema, for example `AgentTurnResult` |
| `tools` | Provider-neutral tool definitions available for this call |
| `temperature` | Overrides the model profile temperature |
| `max_tokens` | Overrides the model profile output-token limit |
| `timeout_seconds` | Per-call timeout |

## ModelResponse

A `ModelResponse` is the normalized result of one model call.

```python
@dataclass
class ModelResponse:
    text: str | None
    structured_output: object | None
    tool_calls: list[dict]
    usage: ModelUsage
    model: str
    provider: str | None
    raw_response: object | None
    finish_reason: str | None
```

## ModelUsage

```python
@dataclass
class ModelUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
```

Usage is always recorded when the provider returns it. For v1, token usage is logged but does not determine energy cost.

## Responsibilities

The model client is responsible for:

- resolving `model_profile` to a LiteLLM model configuration;
- calling LiteLLM;
- passing provider-neutral messages and tool schemas;
- requesting structured output where supported;
- parsing structured output into the expected schema;
- capturing token usage;
- attaching run, turn, and agent metadata to logs;
- normalizing common provider errors;
- applying the v1 retry policy;
- supporting fake responses for tests.

## Retry policy

For v1:

- retry transient provider/network failures once;
- do not retry authentication errors;
- do not silently switch models;
- invalid structured output is handled by the agent loop's repair policy, not by the model client;
- all attempts are logged.

Fallback models are deferred. They are easier to add once the run transcript and error model are stable.

## Non-goals

The model client does not:

- implement multi-agent logic;
- choose the next agent;
- interpret handoff semantics;
- own the energy budget;
- own memory or context loading;
- hide every provider-specific capability forever.

Provider-specific features may be exposed later through explicit capability flags or profile settings.

## Consequences

- The agent loop remains independent from LiteLLM calls.
- Tests can run without external model providers.
- Logging and usage tracking have one natural integration point.
- Later proxy-based or native-provider clients can be added without rewriting the orchestration loop.
