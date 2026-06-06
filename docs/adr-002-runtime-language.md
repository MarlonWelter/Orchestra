# ADR-002: Runtime Language and Project Shape

## Status

Accepted

## Context

ADR-001 chooses LiteLLM as the v1 model access layer. LiteLLM is primarily a Python library. This implicitly affects the runtime language of Orchestra and should be made explicit.

The project could be built in several ways:

| Option | Summary |
|---|---|
| Python-first | Build the first engine, CLI, and model layer in Python |
| .NET-first with LiteLLM Proxy | Build the engine in .NET and access models through a LiteLLM HTTP proxy |
| Hybrid | Build orchestration in .NET and run a Python model-service sidecar |

## Decision

Build **Orchestra v1 as a Python-first project**.

This does not mean the project can never support .NET or other runtimes. It means the first implementation should optimize for the shortest path to a working engine:

- direct LiteLLM library integration;
- simple CLI execution;
- simple file-based configuration;
- fast iteration on the agent loop;
- minimal infrastructure requirements.

## Reasoning

**1. LiteLLM library integration is simplest in Python.**  
Using LiteLLM directly avoids requiring a proxy for the first version.

**2. The core problem is orchestration, not language integration.**  
The important early design work is the agent loop, handoff protocol, prompt construction, run history, and energy budget. Python keeps this lightweight.

**3. Python is better aligned with the existing LLM tooling ecosystem.**  
Most LLM libraries, examples, model utilities, and agent frameworks are Python-first.

**4. A .NET version remains possible later.**  
If Orchestra develops a stable protocol, a .NET implementation can be added later. The important boundary is the protocol, not the first runtime.

## Consequences

- v1 implementation code should be Python.
- The repository should initially avoid multi-language project complexity.
- Documentation should describe Orchestra concepts independently from Python where possible.
- The handoff protocol, config format, and run transcript format should be language-neutral.
- A future .NET implementation should be treated as a separate runtime using the same concepts and file formats, not as a v1 requirement.
