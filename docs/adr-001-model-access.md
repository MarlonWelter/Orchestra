# ADR-001: Model Access via LiteLLM

## Status

Accepted

## Context

Orchestra is designed so that each agent can run on a different model backend — one agent on Claude, another on GPT-4o, another on a local Ollama model. This means the engine must talk to multiple LLM providers without baking provider-specific code into agent logic.

Three architectural options were considered:

| Approach | Summary |
|---|---|
| Direct provider SDKs | Call OpenAI, Anthropic, Google, etc. using their native SDKs directly |
| LiteLLM (library) | Universal Python library that wraps many providers behind a single OpenAI-compatible interface |
| LLM Gateway (proxy) | A separate HTTP service (e.g. LiteLLM Proxy, OpenRouter) that Orchestra routes requests through |

### Direct provider SDKs

Each provider has its own client, auth pattern, and response format. Switching models requires code changes that touch the engine layer. This directly contradicts Orchestra's design goal of keeping agent identity separate from model backend — rejected for v1.

### LiteLLM Gateway / Proxy

A proxy adds production-grade features: automatic failover, semantic caching, spend limits, audit logs, central credential management, and request-level budget enforcement before hitting the provider. This is a good upgrade path for power users.

However, it requires users to run a sidecar service before they can try Orchestra. That is too much operational overhead for a first open-source version — deferred.

### LiteLLM (library)

LiteLLM as a library normalizes provider APIs into a single interface. A model can be addressed as a string such as `openai/gpt-4o`, `anthropic/claude-sonnet-4-6`, or `ollama/llama3`.

## Decision

Use **LiteLLM as a Python library dependency** for v1, wrapped behind a thin internal Orchestra model client.

Orchestra agent logic must not call `litellm.completion(...)` directly. All calls go through a small internal model client boundary responsible for request normalization, logging, error handling, usage capture, retry policy, energy accounting, and test fakes.

## Reasoning

**1. Direct mapping to Orchestra's architecture.**  
Agents should be independent from model backends. Each agent references a `model_profile`, not a raw provider/model string. The model profile is resolved at runtime by the engine.

**2. Model profiles scale better than inline model strings.**  
A team can assign several agents to the same profile, for example `frontier`, `cheap`, or `local`. Swapping the model for an entire class of agents then requires one config change instead of editing every agent.

**3. A thin wrapper is the right boundary.**  
Even though LiteLLM provides the provider abstraction, Orchestra still needs one internal boundary for concerns that belong to the engine: run IDs, turn IDs, structured output validation, retries, usage normalization, logging, and tests.

**4. Orchestra's sequential design sidesteps LiteLLM's main weakness.**  
LiteLLM's concurrency and asyncio overhead matters mainly for parallel workloads. Orchestra v1 is explicitly turn-based and sequential, so this is not a major concern.

**5. Local models remain easy.**  
Users who prefer not to pay API costs can point a model profile at something like `ollama/mistral` with no engine changes. This lowers the barrier to adoption.

**6. Clear upgrade path.**  
The internal model client can later route through LiteLLM Proxy without changing agent logic. The proxy path is deferred, not rejected.

## Engine interface

The intended config pattern:

```yaml
models:
  frontier:
    provider: litellm
    model: anthropic/claude-sonnet-4-6
    temperature: 0.2

  cheap:
    provider: litellm
    model: openai/gpt-4o-mini
    temperature: 0.2

  local:
    provider: litellm
    model: ollama/mistral
    temperature: 0.2

agents:
  risk_analyst:
    model_profile: frontier
    role_prompt: agents/risk_analyst.md
```

The intended engine call pattern:

```python
request = ModelRequest(
    run_id=run.id,
    turn_id=turn.id,
    agent_id=agent.id,
    model_profile=agent.model_profile,
    messages=build_messages(agent, conversation_history),
    response_schema=AgentTurnResult,
)

response = model_client.complete(request)
```

The internal `ModelClient` resolves the model profile and calls LiteLLM.

## Energy accounting

For v1, energy accounting remains intentionally simple:

- each model call costs `1` energy;
- token usage is logged on every turn;
- token usage does not yet affect budget enforcement.

This keeps the first execution loop easy to reason about while preserving the data needed for later cost-aware accounting.

## Consequences

- LiteLLM becomes a required dependency for the Python v1 runtime.
- Provider API keys are configured per-provider in the environment, using LiteLLM's standard conventions.
- Agent configs reference `model_profile` instead of raw model names.
- Orchestra owns a small internal model client abstraction even though LiteLLM handles provider abstraction.
- Tool definitions and structured outputs should use provider-neutral schemas compatible with LiteLLM.
- If a user wants gateway features later, they can run LiteLLM Proxy and point model profiles at it without changing agent definitions.
