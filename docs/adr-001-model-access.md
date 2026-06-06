# ADR-001: Model Access via LiteLLM

## Status

Accepted

## Context

Orchestra is designed so that each agent can run on a different model backend — one agent on Claude, another on GPT-4o, another on a local Ollama model. This means the engine must talk to multiple LLM providers without baking provider-specific code into agent logic.

Three architectural options were considered:

| Approach | Summary |
|---|---|
| Direct provider SDKs | Call OpenAI, Anthropic, Google, etc. using their native SDKs directly |
| LiteLLM (library) | Universal Python library that wraps 100+ providers behind a single OpenAI-compatible interface |
| LLM Gateway (proxy) | A separate HTTP service (e.g. LiteLLM Proxy, OpenRouter) that Orchestra routes requests through |

### Direct provider SDKs

Each provider has its own client, auth pattern, and response format. Switching models requires code changes that touch the engine layer. This directly contradicts Orchestra's design goal of keeping agent identity separate from model backend — rejected.

### LiteLLM Gateway / Proxy

Adds production-grade features: automatic failover, semantic caching, spend limits, audit logs. Budget enforcement can happen at the request level (reject before hitting the provider). Good upgrade path for power users.

However, it requires users to run a sidecar service before they can try Orchestra. Over-engineered for an open-source framework at v1 — deferred.

### LiteLLM (library)

Open-source Python library that normalizes provider APIs into a single interface. A model is just a string: `"openai/gpt-4o"`, `"anthropic/claude-sonnet-4-6"`, `"ollama/llama3"`. Widely adopted (Agent Zero, DSPy, Agno, and others use it).

## Decision

Use **LiteLLM as a Python library dependency** for v1.

## Reasoning

**1. Direct mapping to Orchestra's architecture.**  
Each agent config stores a `model` string. The engine calls `litellm.completion(model=agent.model, messages=...)`. Agent identity and model backend are cleanly separated with no extra abstraction needed.

**2. Orchestra's sequential design sidesteps LiteLLM's main weakness.**  
LiteLLM's concurrency and asyncio overhead only matters for parallel workloads. Orchestra is explicitly turn-based and sequential, so this does not apply.

**3. Energy budget comes for free.**  
Every LiteLLM response includes `response.usage.total_tokens`. The engine can translate token usage into energy cost per turn without extra instrumentation.

**4. Local models at zero cost.**  
Users who prefer not to pay API costs can point any agent at `"ollama/mistral"` with no code changes. This lowers the barrier to adoption.

**5. Clear upgrade path.**  
LiteLLM as a library can be transparently pointed at a LiteLLM Proxy if a user later wants gateway features. Orchestra's engine code does not need to change.

## Engine interface

The intended call pattern:

```python
# Agent config (stored, serializable)
agent = {
    "id": "risk_analyst",
    "model": "anthropic/claude-sonnet-4-6",  # just a string
    "role_prompt": "You are a skeptical risk analyst...",
}

# Engine call — identical for every agent regardless of provider
response = litellm.completion(
    model=agent["model"],
    messages=build_prompt(agent, conversation_history),
)

# Energy accounting
energy_used = response.usage.total_tokens / TOKENS_PER_ENERGY_UNIT
```

The `model` string is the only thing that changes between a cheap local agent and an expensive frontier model.

## Consequences

- LiteLLM becomes a required dependency.
- Provider API keys are configured per-provider in the environment (LiteLLM reads standard env vars like `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).
- Tool definitions must use LiteLLM's unified schema rather than provider-specific formats — this is a benefit, not a constraint.
- If a user wants gateway features (failover, caching, spend limits), they can run a LiteLLM Proxy and point Orchestra at it without any engine changes.
