# ADR-008: Structured Output Strategy

## Status

Accepted

## Context

The engine needs a parsed, validated `AgentTurnResult` object after every model call. Models return text. Providers differ in how strongly they support structured output. A strategy is needed that works reliably across frontier hosted models, alternative providers, and local models via Ollama.

Three mechanisms exist:

| Mechanism | Description |
|---|---|
| Native JSON schema enforcement | Provider guarantees output matches a specific schema |
| JSON mode | Provider guarantees valid JSON, but not a specific structure |
| Prompt-only | Rely on system prompt and output reminder; parse and validate locally |
| Tool / function calling | Define `AgentTurnResult` as a tool schema and force the model to call it |

No single mechanism is both reliable and universally compatible across providers.

## Decision

Use a **layered enforcement strategy**. Every model call applies all three layers simultaneously.

| Layer | Mechanism | Owner |
|---|---|---|
| 1 | `response_format=AgentTurnResult` via LiteLLM | `ModelClient` |
| 2 | Schema, example, and JSON reminder in prompt | `PromptBuilder` |
| 3 | Local Pydantic validation; repair on failure | `ModelClient` + Engine |

**Local Pydantic validation is the source of truth.** Provider-level schema enforcement is treated as a reliability improvement, not a guarantee. The engine trusts its own parse result, not the provider's.

Tool and function calling are not used as the default structured output mechanism in v1. They introduce two output channels, provider-specific differences in tool call format, and additional transcript complexity. `response_format` covers the same need more cleanly.

## Layer 1: `response_format` via LiteLLM

The `ModelClient` always passes `response_format=AgentTurnResult` to LiteLLM:

```python
response = litellm.completion(
    model=resolved_model,
    messages=messages,
    response_format=AgentTurnResult,
)
```

LiteLLM uses the strongest available mechanism for the active provider — native JSON schema enforcement, JSON mode, or prompt injection — without the engine needing to know which provider it is talking to.

Provider support varies and evolves. The engine must not assume all models enforce schemas equally strongly. Layer 1 improves reliability; it does not eliminate the need for local validation.

## Layer 2: Prompt-based enforcement

`PromptBuilder` is responsible for:

- including the full `AgentTurnResult` JSON schema with a concrete example in the system prompt;
- appending a short output reminder at the end of the current input: `Return only a valid AgentTurnResult JSON object.`

These are present on every call, independent of provider or model profile. For providers and local models where Layer 1 degrades or is unsupported, Layer 2 is the primary mechanism guiding model output.

## Layer 3: Local validation and repair

After every model call, the `ModelClient` parses and validates the response locally:

```python
content = response.choices[0].message.content

try:
    parsed = AgentTurnResult.model_validate_json(content)
except ValidationError as ex:
    raise InvalidOutputError(
        raw_content=content,
        validation_error=ex,
        model_response=response,
    )
```

On success, `ModelResponse` contains both the raw text and the parsed object:

```python
ModelResponse(
    text=content,
    structured_output=parsed,
    usage=...,
    ...
)
```

Storing both allows transcripts to record the raw model output alongside the validated result, which is useful for debugging and future repair analysis.

On failure, `ModelClient` raises `InvalidOutputError`. The engine catches this and triggers the repair policy defined in ADR-003: one repair attempt asking the same model to reformat its previous answer into valid JSON, then escalation to the manager on failure.

The `ModelClient` raises the error. It does not implement repair logic itself.

## Local models

Local model behavior via Ollama and similar backends should be treated as best-effort. Some local backends accept schema hints; others do not. Orchestra expects weaker structured output compliance from local models than from frontier hosted models. Layer 2 carries more of the load in these cases, and the repair path will trigger more frequently. This is acceptable for v1.

## Model profile override (deferred)

For v1, `response_format=AgentTurnResult` is used on every model call. If a specific model is found to behave poorly with `response_format`, a per-profile override can be added to the config:

```yaml
models:
  local:
    provider: litellm
    model: ollama/mistral
    structured_output:
      mode: prompt_only
```

This config key is not implemented until a real model requires it. Speculative compatibility workarounds are deferred until there is a concrete reason to add them.

## Consequences

- `ModelClient` always passes `response_format=AgentTurnResult` to LiteLLM.
- `ModelClient` always validates `response.choices[0].message.content` locally with Pydantic.
- `PromptBuilder` always includes the schema and output reminder in the prompt.
- `InvalidOutputError` carries the raw content, the validation error, and the raw model response for logging and repair.
- `ModelResponse` stores both `text` (raw content) and `structured_output` (parsed object).
- Tool and function calling are not the default structured output path in v1.
- Provider-level schema enforcement is an optimization, not a guarantee. Local validation is the source of truth.
