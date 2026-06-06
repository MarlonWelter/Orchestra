# Orchestra v1 — Implementation Plan

## Goal

Build the smallest working version of Orchestra that can execute a turn-based
multi-agent workflow end-to-end using a fake model client. The first
implementation proves the core engine loop before any real model provider is
connected.

## Definition of v1

v1 is complete when the following scenario works:

```text
Given:
- a valid team.yaml
- at least two role prompt files
- a fake model client with predefined AgentTurnResult responses

When:
- orchestra run --team examples/investment_team/team.yaml --input "..." is executed

Then:
- the config is loaded and validated
- a run directory is created under .orchestra/runs/<run_id>/
- the entry agent receives the external input
- agents hand off to each other using AgentTurnResult
- energy decreases by 1 per model call
- turn files are written incrementally
- run.json is updated after each turn
- the run ends with status completed, failed, or exhausted
- the final answer is printed to the CLI
```

LiteLLM integration is explicitly out of scope until the fake-model vertical
slice passes end-to-end.

---

## Module dependency order

The build order follows the dependency graph. Each module depends only on
modules above it.

```
schemas.py + errors.py
    ├── config.py
    ├── transcript_store.py
    └── model_client.py (interface only)
            └── prompt_builder.py
                    └── engine.py
                            └── cli.py
```

`tests/fakes.py` depends on the `ModelClient` interface and `schemas.py` and
is written alongside Phase 5.

---

## Phase 0 — Project skeleton

**Build:**

- `pyproject.toml` with all dependencies:
  - runtime: `pydantic>=2`, `pyyaml`, `typer`, `rich`
  - dev: `pytest`, `pytest-cov`, `ruff`, `mypy`
  - do **not** add `litellm` yet — the fake-model path must run without it
- Full directory structure:

```text
src/orchestra/
├── __init__.py
├── cli.py           (empty stub)
├── engine.py        (empty stub)
├── config.py        (empty stub)
├── prompt_builder.py (empty stub)
├── model_client.py  (empty stub)
├── transcript_store.py (empty stub)
├── schemas.py       (empty stub)
└── errors.py        (empty stub)

tests/
├── fakes.py
├── unit/
├── integration/
└── fixtures/
    └── investment_team/
        ├── team.yaml
        └── agents/
            ├── george.md
            └── warren.md

examples/
└── investment_team/
    ├── team.yaml
    └── agents/
        ├── george.md
        ├── warren.md
        ├── elon.md
        └── klaus.md
```

- `.gitignore` covering `.venv/`, `__pycache__/`, `.orchestra/`, `*.pyc`,
  `.mypy_cache/`, `.ruff_cache/`

**Done when:**
- `uv sync` installs cleanly
- `pytest` runs with zero tests and exits 0
- `orchestra` CLI entry point exists and is importable
- All package imports resolve from `src/orchestra`

---

## Phase 1 — Foundations: schemas and errors

**Build: `schemas.py`**

All shared data contracts live here to prevent circular imports. Use Pydantic
models for all externally serialized objects.

Types to define:

```python
# Enumerations
class HandoffType(str, Enum):
    continue_ = "continue"
    return_ = "return"
    final = "final"

class RunStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    exhausted = "exhausted"

class TurnStatus(str, Enum):
    completed = "completed"
    failed = "failed"

# Handoff and agent turn result (ADR-003)
class Handoff(BaseModel):
    type: Literal["continue", "return", "final"]
    recipient: str
    task: str | None = None

class AgentTurnResult(BaseModel):
    agent_id: str
    response: str
    handoff: Handoff
    notes: list[str] = []

# Energy
class EnergyState(BaseModel):
    initial: int
    used: int
    remaining: int

# Model client contracts (ADR-004)
class ModelUsage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

class ModelRequest(BaseModel):
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

class ModelResponse(BaseModel):
    text: str | None
    structured_output: AgentTurnResult | None
    tool_calls: list[dict] = []
    usage: ModelUsage
    model: str
    provider: str | None
    raw_response: object | None
    finish_reason: str | None

# Repair and validation (for transcript)
class RepairAttempt(BaseModel):
    raw_content: str
    validation_error: str
    repair_raw_content: str | None
    repair_valid: bool

class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []

# Run and turn state (ADR-005)
class TurnRecord(BaseModel):
    turn_id: str
    run_id: str
    index: int
    agent_id: str
    sender_agent_id: str
    status: TurnStatus
    ...

class RunState(BaseModel):
    run_id: str
    team_id: str
    status: RunStatus
    energy: EnergyState
    turns: list[...]
    final_answer: str | None
    error: str | None
    ...
```

**Build: `errors.py`**

```python
class OrchestraError(Exception): ...

class ConfigError(OrchestraError): ...

class PromptAssemblyError(OrchestraError): ...

class ModelProviderError(OrchestraError):
    # carries: message, provider, model, is_retryable

class InvalidOutputError(OrchestraError):
    # carries: raw_content, validation_error, model_response (optional)

class HandoffValidationError(OrchestraError):
    # carries: reason, agent_id, handoff

class EnergyExhaustedError(OrchestraError): ...

class TranscriptWriteError(OrchestraError):
    # carries: path, original_error
```

**Done when:**
- `AgentTurnResult` validates correct JSON and rejects invalid handoff types
- All error classes are importable without circular dependencies
- `InvalidOutputError` carries `raw_content`, `validation_error`, and optional
  `model_response`

---

## Phase 2 — Config loading and validation

**Build: `config.py`**

Typed config objects:

```python
@dataclass
class ModelProfileConfig:
    provider: str
    model: str
    temperature: float | None
    max_tokens: int | None
    timeout_seconds: int | None

@dataclass
class AgentConfig:
    id: str
    name: str
    role_prompt: Path
    model_profile: str
    can_finalize: bool
    can_handoff_to: list[str] | None  # None = all agents allowed

@dataclass
class TeamConfig:
    id: str
    name: str
    entry_agent: str
    default_energy: int
    models: dict[str, ModelProfileConfig]
    agents: dict[str, AgentConfig]
```

Validation rules (all raise `ConfigError`):

- `entry_agent` exists in `agents`
- every `model_profile` reference resolves to a key in `models`
- every `role_prompt` file exists on disk
- every `can_handoff_to` recipient exists in `agents`
- at least one agent has `can_finalize: true`
- `default_energy > 0`
- all paths resolved relative to the config file location, not the working
  directory

Example fixture config for tests (`tests/fixtures/investment_team/team.yaml`):

```yaml
team:
  id: investment_team
  name: Investment Team
  entry_agent: george
  default_energy: 20

models:
  fake:
    provider: fake
    model: fake/default

agents:
  george:
    name: George
    role_prompt: agents/george.md
    model_profile: fake
    can_finalize: true
    can_handoff_to: [warren]

  warren:
    name: Warren
    role_prompt: agents/warren.md
    model_profile: fake
    can_finalize: false
    can_handoff_to: [george]
```

**Tests: `tests/unit/test_config.py`**

- Valid config loads and returns typed objects
- Missing `entry_agent` raises `ConfigError`
- Missing `model_profile` reference raises `ConfigError`
- Missing role prompt file raises `ConfigError`
- Invalid `can_handoff_to` recipient raises `ConfigError`
- No agent with `can_finalize: true` raises `ConfigError`
- `default_energy <= 0` raises `ConfigError`

**Done when:** all `test_config.py` tests pass; fixture config validates

---

## Phase 3 — Transcript store

**Build: `transcript_store.py`**

```python
class TranscriptStore:
    def start_run(self, run_state: RunState) -> None: ...
    def write_turn(self, turn_record: TurnRecord) -> None: ...
    def update_run(self, run_state: RunState) -> None: ...
    def complete_run(self, run_state: RunState, final_answer: str) -> None: ...
    def fail_run(self, run_state: RunState, error: str) -> None: ...
    def exhaust_run(self, run_state: RunState) -> None: ...
```

Writes to `.orchestra/runs/<run_id>/` relative to the current working
directory (ADR-007). Each turn file is written when that turn completes —
not at the end of the run. `run.json` is updated after every turn.

**Tests: `tests/unit/test_transcript_store.py`** (use `tmp_path` fixture)

- `start_run` creates run directory and writes `run.json` with status `running`
- `write_turn` creates a turn file with correct fields
- `run.json` turn index updates after each `write_turn`
- `complete_run` sets status to `completed` and records `final_answer`
- `fail_run` sets status to `failed` and records error summary
- `exhaust_run` sets status to `exhausted`
- Partial turn file is written on unrecoverable error (best-effort)

**Done when:** all `test_transcript_store.py` tests pass with no model calls

---

## Phase 4 — Prompt builder

**Build: `prompt_builder.py`**

```python
class PromptBuilder:
    def build_messages(
        self,
        team: TeamConfig,
        active_agent: AgentConfig,
        run: RunState,
        current_task: str,
        sender_agent_id: str,
        energy: EnergyState,
    ) -> list[dict]:
        ...

    def _build_system_prompt(self, team: TeamConfig) -> str: ...
    def _load_role_description(self, agent: AgentConfig) -> str: ...
    def _build_current_input(self, ...) -> str: ...
    def _format_history(self, turns: list[TurnRecord]) -> str: ...
```

Output shape (ADR-006):

```python
[
    {"role": "system", "content": system_prompt},
    {"role": "system", "content": role_description},
    {"role": "user",   "content": current_input},
]
```

Current input must include: active agent, sender agent (`external` on turn 1),
task, energy remaining, allowed recipients, whether the agent can finalize,
formatted run history as transcript text, and a short JSON-only reminder at
the end.

History is formatted as structured transcript text — not synthetic alternating
`user`/`assistant` messages (ADR-006).

**Tests: `tests/unit/test_prompt_builder.py`**

- Output is exactly three messages
- First system message contains the `AgentTurnResult` schema and team roster
- Second system message contains the active agent's role prompt
- Current input contains sender, task, energy, allowed recipients, `can_finalize`
- Current input ends with JSON-only reminder
- First turn renders sender as `external`
- History is formatted as transcript text

**Done when:** all `test_prompt_builder.py` tests pass; no model calls made

---

## Phase 5 — Model client interface and FakeModelClient

**Build: `model_client.py`**

Define the interface only. Do not implement LiteLLM yet.

```python
class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

No production module may import from `tests/`. The fake implementation lives
exclusively in `tests/fakes.py`.

**Build: `tests/fakes.py`**

```python
class FakeModelClient:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise StopIteration("FakeModelClient ran out of responses")
        next_response = self.responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return next_response
```

The fake must support: valid `ModelResponse` objects, `InvalidOutputError`,
`ModelProviderError`, and repair-specific responses. Storing `.requests`
enables test assertions on what was sent to the model, not just what came back.

**Done when:** `FakeModelClient` can drive a multi-turn sequence and raise
configured errors at the right turn; `StopIteration` surfaces test setup
mistakes immediately

---

## Phase 6 — Handoff validation and engine loop

### 6a — Handoff validation

Validation logic can live as a standalone function in `engine.py`.

Rules (all raise `HandoffValidationError`):

- `agent_id` in `AgentTurnResult` matches the active agent id
- `response` is non-empty
- handoff type is one of `continue`, `return`, `final`
- for `continue` and `return`: recipient exists in team config
- for `continue` and `return`: recipient is in `can_handoff_to` if routing
  rules are configured (if `can_handoff_to` is omitted, all agents are allowed)
- for `final`: active agent has `can_finalize: true`
- for `final`: recipient is `"external"`

Important: `return` does **not** require the recipient to be a prior sender.
It is routed identically to `continue`. The distinction is semantic only and
is recorded in the transcript for human readability (ADR-003).

**Tests: `tests/unit/test_handoff_validation.py`**

- Valid `continue` to an allowed recipient passes
- Valid `return` to a non-previous agent passes
- Unknown recipient raises `HandoffValidationError`
- Recipient not in `can_handoff_to` raises `HandoffValidationError`
- `final` by agent with `can_finalize: false` raises `HandoffValidationError`
- `final` by agent with `can_finalize: true` passes
- Empty `response` raises `HandoffValidationError`
- `agent_id` mismatch raises `HandoffValidationError`

### 6b — Engine loop

Implement `engine.py` following the ADR-009 sequence exactly.

**Per-turn sequence:**

1. Initialise turn record in memory
2. Build prompt via `PromptBuilder`
3. Call `ModelClient.complete(request)`
4. Attempt `AgentTurnResult.model_validate_json(content)` — on failure, execute
   repair sequence
5. Deduct energy for all model calls made this turn
6. Validate handoff
7. Write turn file via `TranscriptStore`
8. Update `run.json`
9. Evaluate termination
10. Select next agent

**Repair sequence (part of the same turn, not a new turn):**

1. Log the invalid output
2. Build a repair prompt (ask the same model to reformat its previous answer)
3. Call `ModelClient.complete(repair_request)` with the same model profile
4. Attempt to parse the repair response
   - Success: use repair result; record `repair_attempted: true` in turn file
   - Failure: raise unrecoverable `InvalidOutputError`

**Manager escalation (on unrecoverable turn failure):**

1. Write the failed turn file
2. If the failed agent is not the entry agent and energy remains: construct
   a failure summary and hand to the entry agent as a new turn
3. Otherwise: mark run as `failed` and write error to `run.json`

**Energy timing:**

- Deduct after each model call that was sent, before writing the turn file
- Repair call costs 1 additional energy
- Errors before a model call is sent cost no energy
- Final check: if a turn produces valid `final` and energy also hits 0, status
  is `completed` — energy exhaustion only applies when the engine needs to
  start another turn

**Tests: `tests/unit/test_energy.py`**

- Normal model call deducts 1 energy
- Repair call deducts 1 additional energy
- `PromptAssemblyError` before call deducts no energy
- Valid `final` in same turn energy hits 0 → `completed`, not `exhausted`

**Tests: `tests/integration/test_engine_with_fake_model.py`**

*Scenario A — happy path:*

```text
Turn 1: external → George  (George continues to Warren)
Turn 2: George   → Warren  (Warren returns to George)
Turn 3: Warren   → George  (George emits final)
```

Expected: `completed`; 3 turn files; energy reduced by 3; `run.json` has 3
turn entries; final answer recorded.

*Scenario B — repair succeeds:*

```text
Turn 2: Warren returns invalid output → repair returns valid result → run continues
```

Expected: Turn 2 file contains `repair_attempted: true` and both raw
responses; energy cost for Turn 2 is 2; run reaches `completed`.

*Scenario C — repair fails, manager escalation:*

```text
Turn 2: Warren returns invalid output → repair also fails → George receives failure summary → final
```

Expected: Turn 2 file records both failed attempts; George receives escalation
task; run reaches `completed` if George can finalize.

*Scenario D — energy exhaustion:*

```text
Run starts with energy=2, requires 3 turns
```

Expected: after Turn 2, energy is 0; engine does not start Turn 3; status
`exhausted`.

*Scenario E — invalid handoff:*

```text
Warren attempts to hand off to an agent not in can_handoff_to
```

Expected: `HandoffValidationError`; failed turn written; escalation attempted.

**Done when:** all five integration scenarios pass using `FakeModelClient` only

---

## Phase 7 — CLI

**Build: `cli.py`**

Use Typer. Two commands for v1:

```bash
orchestra validate --team examples/investment_team/team.yaml
orchestra run --team examples/investment_team/team.yaml --input "Analyze Alphabet"
```

`validate` checks config, role prompt paths, model profiles, and routing
rules — no model calls, exits 0 on success.

`run` executes the team and prints using Rich:

```text
Starting run run_2026-06-06_140501_ab12...

  [Turn 1] external → George
  [Turn 2] George   → Warren   "Analyze Alphabet from a conservative perspective..."
  [Turn 3] Warren   → George   "Use this conservative assessment in the synthesis."
  [Turn 4] George   → final

Energy used: 4 / 20
Transcript: .orchestra/runs/run_2026-06-06_140501_ab12/

Final answer:
─────────────────────────────────────────────────────────────────
Alphabet remains a high-quality long-term holding...
```

On failure:

```text
  [Turn 3] Warren   → ? (repair failed)

Run failed at turn 3: invalid output after repair attempt.
Transcript: .orchestra/runs/run_2026-06-06_140501_ab12/
```

On exhaustion:

```text
Run exhausted after 20 turns. No final answer produced.
Transcript: .orchestra/runs/run_2026-06-06_140501_ab12/
```

**Tests: `tests/integration/test_cli_run_example_team.py`**

- `orchestra validate` exits 0 on valid fixture config
- `orchestra validate` exits non-zero with message on invalid config
- `orchestra run` with `FakeModelClient` completes and prints final answer
- `orchestra run` on exhaustion prints exhaustion message and transcript path

**Note on `.orchestra/runs/`:** These directories contain full assembled
prompts and may include sensitive content. The CLI should remind users of this
on first run. Documentation must note that `.orchestra/runs/` should be treated
as sensitive and added to `.gitignore`.

**Done when:** all CLI tests pass; `orchestra run` executes from the terminal
against fixture config

---

## ✓ Milestone: Vertical slice complete

At the end of Phase 7:

- All unit tests pass
- All integration tests pass with `FakeModelClient` — no network, no API keys
- `orchestra run` executes a multi-turn workflow, writes a complete incremental
  transcript, and prints the final answer
- No LiteLLM dependency has been exercised

Do not proceed to Phase 8 until this milestone is confirmed.

---

## Phase 8 — LiteLLM integration

Start only after the fake-model vertical slice passes.

**Build: `LiteLLMModelClient` in `model_client.py`**

```python
response = litellm.completion(
    model=resolved_model,
    messages=messages,
    response_format=AgentTurnResult,
)

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

Responsibilities:

- resolve `model_profile` to a LiteLLM model string
- pass `response_format=AgentTurnResult` (ADR-008)
- always validate locally with Pydantic — provider schema enforcement is an
  optimization, not a guarantee
- raise `InvalidOutputError` on parse failure
- retry transient `ModelProviderError` once; do not retry auth errors
- normalize `ModelUsage` from `response.usage`

Add `litellm` to `pyproject.toml` at this point.

Provider selection is config-driven: `provider: litellm` in a model profile
uses `LiteLLMModelClient`; `provider: fake` uses `FakeModelClient`. No CLI
flag needed.

**Automated tests:** unit tests only, with mocked LiteLLM responses. Do not
require API keys in CI.

**Manual verification:**

- Run fixture team against `anthropic/claude-sonnet-4-6`
- Run fixture team against `openai/gpt-4o-mini`
- Confirm `AgentTurnResult` is parsed correctly
- Confirm token usage is recorded in turn files
- Confirm repair path triggers when structured output is not honoured

**Done when:** `orchestra run` completes a real multi-turn run against at least
one live provider and writes a valid transcript

---

## Phase 9 — Example team and documentation

**Build:**

- Full `examples/investment_team/` with real role prompts for George, Warren,
  Elon, and Klaus per `config-format.md`
- `examples/investment_team/team.yaml` with `provider: litellm` model profiles
- Role prompts should be minimal — their purpose is to test routing and
  transcripts, not to produce perfect investment analysis
- Update `README.md` with: what Orchestra is, quickstart, and a description
  of the example team:

```bash
uv sync
orchestra validate --team examples/investment_team/team.yaml
orchestra run --team examples/investment_team/team.yaml --input "Analyze Alphabet"
```

- Note in README that `.orchestra/runs/` may contain full prompts and should
  be added to `.gitignore`

**Done when:** `orchestra run --team examples/investment_team/team.yaml` works
with a live provider and produces a readable multi-agent transcript

---

## Explicitly out of scope for v1

Do not implement any of the following during v1:

- agent memory and context folders
- tool use (web search, APIs, file access)
- parallel agent execution
- run resume
- database-backed transcript storage
- LiteLLM Proxy integration
- cost-aware or token-based energy accounting
- automatic or scheduled inputs
- UI or visualization tooling
- long-context compression or relevance filtering
- per-agent fallback model profiles
- nested teams

These can be designed after the basic sequential engine works reliably.

---

## Recommended PR strategy

Deliver the vertical slice in five pull requests:

**PR 1 — Skeleton, schemas, and config**

```text
pyproject.toml
src/orchestra/ (all stubs)
schemas.py
errors.py
config.py
tests/fixtures/investment_team/
tests/unit/test_config.py
```

No model logic. Safe to review and merge independently.

**PR 2 — Transcript store and prompt builder**

```text
transcript_store.py
prompt_builder.py
tests/unit/test_transcript_store.py
tests/unit/test_prompt_builder.py
```

No model calls required.

**PR 3 — Fake-model engine loop**

```text
model_client.py (interface only)
tests/fakes.py
engine.py
tests/unit/test_handoff_validation.py
tests/unit/test_energy.py
tests/integration/test_engine_with_fake_model.py (all five scenarios)
```

Full engine behavior tested without any network dependency.

**PR 4 — CLI and example run**

```text
cli.py
tests/integration/test_cli_run_example_team.py
examples/investment_team/ (minimal prompts)
README.md quickstart
```

**PR 5 — LiteLLM integration**

```text
LiteLLMModelClient in model_client.py
litellm added to pyproject.toml
examples/investment_team/ (full prompts)
manual smoke test documentation
```

Only after the fake-model vertical slice (PRs 1–4) is merged and confirmed.

---

## Recommended implementation order

For a single developer working sequentially:

1. `pyproject.toml` and package structure
2. `schemas.py`
3. `errors.py`
4. `config.py`
5. Config tests and fixture files
6. `transcript_store.py`
7. Transcript store tests
8. `prompt_builder.py`
9. Prompt builder tests
10. `model_client.py` interface
11. `tests/fakes.py`
12. Handoff validation logic
13. Handoff validation tests
14. Energy accounting logic and tests
15. `engine.py` — happy path
16. Engine integration test: Scenario A
17. `engine.py` — repair path
18. Engine integration test: Scenarios B and C
19. `engine.py` — exhaustion and invalid handoff
20. Engine integration tests: Scenarios D and E
21. `cli.py` — `validate` command
22. `cli.py` — `run` command
23. CLI integration tests
24. Example team (minimal prompts)
25. README quickstart
26. `LiteLLMModelClient`
27. Manual real-model smoke test
