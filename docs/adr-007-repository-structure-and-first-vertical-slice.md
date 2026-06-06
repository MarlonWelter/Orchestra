# ADR-007: Repository Structure and First Vertical Slice

## Status

Accepted

## Context

Orchestra now has initial architectural decisions for model access, runtime language, handoff protocol, prompt assembly, and transcript storage. The next step is to define a small repository structure that supports the first implementation without over-engineering.

The first implementation should prove the core loop before adding real model providers, tools, memory, parallel execution, or UI features.

## Decision

Use a modern Python `src/` layout with a small flat module structure.

The first milestone must work end-to-end with a fake model client before LiteLLM is connected.

## Repository layout

```text
orchestra/
├── README.md
├── pyproject.toml
├── uv.lock                    # if using uv
├── .gitignore
├── docs/
│   ├── adr-001-model-access.md
│   ├── adr-002-runtime-language.md
│   ├── adr-003-agent-handoff-protocol.md
│   ├── adr-004-model-client-contract.md
│   ├── adr-005-run-state-and-transcripts.md
│   ├── adr-006-prompt-assembly.md
│   ├── adr-007-repository-structure-and-first-vertical-slice.md
│   └── config-format.md
│
├── src/
│   └── orchestra/
│       ├── __init__.py
│       ├── cli.py
│       ├── engine.py
│       ├── config.py
│       ├── prompt_builder.py
│       ├── model_client.py
│       ├── transcript_store.py
│       ├── schemas.py
│       └── errors.py
│
├── examples/
│   └── investment_team/
│       ├── team.yaml
│       └── agents/
│           ├── george.md
│           ├── warren.md
│           ├── elon.md
│           └── klaus.md
│
└── tests/
    ├── fakes.py
    ├── unit/
    │   ├── test_config.py
    │   ├── test_prompt_builder.py
    │   ├── test_handoff_validation.py
    │   ├── test_energy.py
    │   └── test_transcript_store.py
    │
    ├── integration/
    │   ├── test_engine_with_fake_model.py
    │   └── test_cli_run_example_team.py
    │
    └── fixtures/
        └── investment_team/
            ├── team.yaml
            └── agents/
                ├── george.md
                └── warren.md
```

## Explicit non-decision: no `utils.py` upfront

Do not create a generic `utils.py` module at project start.

Generic utility modules tend to become dumping grounds for unrelated helpers. Create specific modules when a real need appears.

Examples:

- path handling belongs near `transcript_store.py` or a future `paths.py` if it grows;
- JSON helpers belong near the component that owns the JSON format;
- validation helpers belong near `config.py` or `schemas.py`.

## Module responsibilities

### `engine.py`

Owns the orchestration loop.

Responsibilities:

- start a run;
- select the entry agent;
- call `PromptBuilder`;
- call `ModelClient`;
- validate `AgentTurnResult`;
- detect invalid structured output;
- trigger one invalid-output repair attempt;
- escalate to a permitted manager/coordinator agent on repair failure where possible;
- mark the turn as failed if repair fails and no recovery path exists;
- apply energy cost;
- write transcripts through `TranscriptStore`;
- resolve the next agent;
- stop on `final`, `failed`, or `exhausted`.

The engine must not call LiteLLM directly, manually assemble prompts, or write transcript files directly.

### `config.py`

Loads and validates team configuration.

Responsibilities:

- parse YAML;
- validate that the entry agent exists;
- validate that model profiles exist;
- validate role prompt paths;
- validate `can_handoff_to` references;
- expose typed config objects.

Suggested types:

- `TeamConfig`
- `AgentConfig`
- `ModelProfileConfig`

### `schemas.py`

Holds shared data contracts to avoid circular imports.

Suggested types:

- `AgentTurnResult`
- `Handoff`
- `HandoffType`
- `RunState`
- `RunStatus`
- `TurnStatus`
- `EnergyState`
- `ModelRequest`
- `ModelResponse`
- `ModelUsage`

### `prompt_builder.py`

Owns all prompt construction.

Responsibilities:

- build the system prompt;
- load role descriptions;
- build current input;
- format run history;
- return the final messages list for the model client.

The prompt builder does not call models and does not validate handoffs.

### `model_client.py`

Defines the internal model client boundary and the LiteLLM-backed implementation.

Responsibilities:

- resolve `model_profile`;
- call LiteLLM;
- request structured output where supported;
- normalize responses;
- normalize usage;
- return `ModelResponse`;
- apply the v1 model retry policy.

Test fakes do not belong in this module. Fake implementations belong under `tests/fakes.py`.

### `transcript_store.py`

Owns `.orchestra/runs/<run_id>/`.

Responsibilities:

- create the run directory;
- write initial `run.json`;
- write turn files incrementally;
- update `run.json` after each turn;
- mark runs as `completed`, `failed`, or `exhausted`.

### `cli.py`

Defines the command-line entry point.

Initial commands:

```bash
orchestra run --team examples/investment_team/team.yaml --input "Analyze Alphabet and decide whether to add to the position."

orchestra validate --team examples/investment_team/team.yaml
```

The `run` command executes a team. The `validate` command checks config, role prompt paths, model profiles, and routing rules without calling any model.

## `.orchestra/` location

For v1, `.orchestra/` is created relative to the current working directory from which `orchestra run` is executed.

Example:

```bash
cd ~/projects/my-analysis
orchestra run --team teams/investment/team.yaml --input "Analyze Alphabet"
```

This creates:

```text
~/projects/my-analysis/.orchestra/runs/<run_id>/
```

Later versions may support explicit workspace directories or configurable transcript locations.

## `pyproject.toml`

Use modern Python packaging with `src/` layout.

Suggested dependencies:

```toml
[project]
name = "orchestra-agent"
version = "0.1.0"
description = "A turn-based multi-agent orchestration engine."
requires-python = ">=3.11"
dependencies = [
    "litellm",
    "pydantic>=2",
    "pyyaml",
    "typer",
    "rich"
]

[project.scripts]
orchestra = "orchestra.cli:app"

[dependency-groups]
dev = [
    "pytest",
    "pytest-cov",
    "ruff",
    "mypy"
]
```

Preferred tools:

- Typer for CLI;
- Pydantic v2 for config and schema validation;
- PyYAML for config files;
- Rich for readable CLI output;
- pytest for tests;
- ruff for formatting and linting;
- mypy later, not necessarily strict from day one.

## Test structure

The first tests must not call real models.

### `tests/fakes.py`

Contains fake test implementations, including `FakeModelClient`.

Example behavior:

```python
class FakeModelClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, request):
        return self.responses.pop(0)
```

The fake client should be able to return valid responses, invalid structured output, provider errors, and repair responses.

### Unit tests

`test_config.py`

- valid team config loads;
- missing entry agent fails;
- missing model profile fails;
- invalid handoff target fails;
- missing role prompt fails.

`test_prompt_builder.py`

- output has the expected three-message shape;
- system prompt contains protocol and team roster;
- role prompt is included separately;
- current input contains sender, task, energy, allowed recipients, and finalization permission;
- history is formatted as transcript text.

`test_handoff_validation.py`

- `continue` to valid agent passes;
- `return` to valid non-previous agent passes;
- `final` by non-finalizing agent fails;
- unknown recipient fails;
- invalid JSON triggers repair path.

`test_transcript_store.py`

- `run.json` is created at run start;
- turn files are written after each turn;
- `run.json` turn index updates;
- status becomes `completed`, `failed`, or `exhausted`.

### Integration tests

Use fake model clients only.

First integration scenario:

```text
George → Warren → George → final
```

Expected result:

- the engine runs multiple turns;
- handoffs are validated;
- energy decreases;
- `run.json` is updated incrementally;
- turn files are written;
- final answer is returned and printed.

Second integration scenario:

```text
Agent returns invalid output → repair succeeds → run continues
```

Third integration scenario:

```text
Agent returns invalid output → repair fails → manager escalation or failed run
```

## First vertical slice

The first milestone is:

```text
Given:
- a team.yaml
- two or more role prompt files
- a fake model client with predefined AgentTurnResult responses

When:
- orchestra run is executed

Then:
- the engine runs multiple turns
- handoffs are validated
- energy decreases
- run.json is updated incrementally
- turn files are written
- final answer is printed
```

## Hard constraint

Do not plug in LiteLLM until the fake-model vertical slice passes.

This prevents provider behavior, API keys, rate limits, and model formatting quirks from obscuring engine bugs.

## Deferred work

Do not include these in the first vertical slice:

- memory;
- tools;
- parallel execution;
- resumability;
- database storage;
- UI visualization;
- automatic scheduled inputs;
- cost-aware model routing;
- LiteLLM Proxy.

## Consequences

- The first codebase stays small and inspectable.
- Most engine behavior can be tested without network calls.
- The architecture is shaped around explicit boundaries: `Engine`, `PromptBuilder`, `ModelClient`, and `TranscriptStore`.
- The project can reach a working loop before integrating real LLM providers.
