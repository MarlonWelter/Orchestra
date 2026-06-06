# ADR-009: Engine Loop Sequence

## Status

Accepted

## Context

The previous ADRs define what each component is responsible for, but not the exact ordered sequence of operations at runtime. Small ordering decisions change observable behavior:

- Does energy get deducted before or after validation?
- Does a failed repair attempt cost energy?
- Is a failed turn written to the transcript before escalation?
- If a valid `final` handoff and energy exhaustion occur in the same turn, what is the run status?
- Is a repair attempt stored inside the original turn or as a separate turn?

These must be explicit before implementation begins.

---

## Run initialization

When `orchestra run` is called:

1. Load and validate team config. Raise `ConfigError` and abort if invalid. No run is created.
2. Generate a unique `run_id`.
3. Create the run directory: `.orchestra/runs/<run_id>/`.
4. Write initial `run.json` with status `running`, `turn_count: 0`, and full energy.
5. Resolve the entry agent from `team.entry_agent`.
6. Begin the first turn with `sender_agent_id = "external"`.

---

## First turn semantics

The first turn is treated as a normal turn with a synthetic external sender.

The initial handoff passed to the engine is:

```json
{
  "sender_agent_id": "external",
  "handoff_type": "external_input",
  "recipient": "<entry_agent_id>",
  "task": "<user's original input>",
  "energy_before": 20
}
```

`external_input` is an internal engine concept only. It is never a valid value in an `AgentTurnResult` emitted by an agent. Its only purpose is to make the first turn structurally identical to all subsequent turns so that `PromptBuilder` and `TranscriptStore` have no special cases.

In the current input assembled by `PromptBuilder`, the first turn reads:

```text
Active agent: George
From: external
Task: Analyze Alphabet and decide whether to add to the position.
Energy remaining: 20 / 20
...
```

---

## Per-turn sequence

For every turn, the engine executes the following steps in order:

1. **Initialise turn record** in memory: `turn_id`, `agent_id`, `sender_agent_id`, `task`, `energy_before`.
2. **Build prompt** via `PromptBuilder`. Raise `PromptAssemblyError` on failure.
3. **Call `ModelClient.complete(request)`**. This is the primary model call.
4. **Parse and validate output**. `ModelClient` attempts `AgentTurnResult.model_validate_json(content)`.
   - On success: continue to step 6.
   - On `InvalidOutputError`: execute the repair sequence (see below). The repair sequence either resolves to a valid result or raises an unrecoverable `InvalidOutputError`.
5. **Deduct energy** for all model calls made in this turn (primary call + repair call if used). One energy unit per model call, regardless of outcome, provided the request was sent.
6. **Validate handoff** on the parsed `AgentTurnResult`.
   - Check handoff type is known.
   - Check recipient exists in team config.
   - Check recipient is in `can_handoff_to` if routing rules are configured.
   - Check `can_finalize` if handoff type is `final`.
   - Raise `HandoffValidationError` on any violation.
7. **Write turn file** to `.orchestra/runs/<run_id>/turns/<NNN>-<agent_id>.json`. Includes both the raw model response and the parsed result.
8. **Update `run.json`**: increment `turn_count`, update `energy_remaining`, append turn to the index.
9. **Evaluate termination** (see termination conditions below).
10. **Select next agent** from the validated handoff recipient and begin the next turn.

---

## Invalid output repair sequence

If `ModelClient` raises `InvalidOutputError` on the primary call, the engine executes a repair attempt as part of the same turn. The repair is not a separate agent turn.

1. Log the invalid output.
2. Build a repair prompt asking the same model to reformat its previous answer into a valid `AgentTurnResult` JSON object. The repair prompt includes the original (invalid) response.
3. Call `ModelClient.complete(repair_request)` with the same model profile.
4. Attempt to parse and validate the repair response.
   - On success: use the repair result as the turn's final parsed result. Record `repair_attempted: true` in the turn file.
   - On failure: the turn is unrecoverable. Record both the original invalid response and the failed repair in the turn file. Raise `InvalidOutputError` (unrecoverable).

Energy is deducted for both the primary call and the repair call (one unit each), provided each request was sent.

If the unrecoverable error is raised, the engine escalates to the manager (see error handling below).

---

## Handoff validation and next-agent selection

After a valid `AgentTurnResult` is obtained:

- `continue`: recipient must exist and be allowed. Engine calls recipient next.
- `return`: recipient must exist and be allowed. Engine calls recipient next. The distinction from `continue` is semantic only — recorded in the transcript, with no difference in engine routing logic.
- `final`: agent must have `can_finalize: true`. Engine terminates the run as `completed` and returns the agent's response as the final answer.

---

## Energy timing

- Energy is deducted **after** a model call returns or fails, provided the request was sent.
- A primary model call costs 1 energy.
- A repair model call costs 1 additional energy.
- Errors that occur before a model call is sent (config errors, prompt assembly errors) cost no energy.
- Energy is deducted before writing the turn file and before evaluating termination, so the transcript always reflects the true remaining energy.

---

## Transcript write timing

Turn files are written **after** all model calls for the turn are complete and energy has been deducted, but **before** the next turn begins. This ensures every turn file reflects the final state of that turn.

If an unrecoverable exception occurs before the turn file is written, the engine attempts a best-effort partial write. A partial write is preferable to no record.

`run.json` is updated after every turn file write, not at the end of the run. A crashed run produces a valid partial transcript.

---

## Termination conditions

| Condition | Trigger | Run status |
|---|---|---|
| Valid `final` handoff | Agent returns `final` and has `can_finalize: true` | `completed` |
| Energy exhausted | Energy reaches 0 and another turn is needed | `exhausted` |
| Unrecoverable turn failure | Repair fails and manager escalation fails or energy is gone | `failed` |
| Manager escalation succeeds | Manager receives failure summary and continues | Run continues |

**Final vs exhausted precedence:** if a turn produces a valid `final` handoff and energy reaches 0 in the same turn, the run status is `completed`. The task finished. Energy exhaustion only applies when the engine needs another turn but cannot start one.

---

## Manager escalation

When a turn fails unrecoverably (repair exhausted, or `HandoffValidationError`, or `PromptAssemblyError`):

1. Record the failed turn in the transcript.
2. If the failed agent is not the entry agent and energy remains: construct a failure summary and hand control to the entry agent (manager) as a new turn. The failure summary describes what went wrong without retrying the failed task.
3. If the failed agent is the entry agent, or energy is exhausted, or the manager turn also fails: end the run with status `failed` and write an error summary to `run.json`.

---

## Error taxonomy

| Error | Raised by | Engine response | Energy cost | Transcript |
|---|---|---|---|---|
| `ConfigError` | Config loader | Abort before run starts | None | No run created |
| `PromptAssemblyError` | `PromptBuilder` | Fail turn, escalate to manager | None (no model call sent) | Turn marked failed |
| `ModelProviderError` | `ModelClient` (transient) | Retry once; if retry fails, fail turn and escalate | 1 per sent request | Turn marked failed |
| `InvalidOutputError` | `ModelClient` (parse failure) | Trigger repair; if repair fails, escalate | 1 + 1 if repair sent | Stored in turn file including raw content |
| `HandoffValidationError` | Engine (validation) | Fail turn, escalate to manager | Already deducted | Turn marked failed |
| `EnergyExhaustedError` | Engine (budget check) | Stop run | N/A | Run marked `exhausted` |
| `TranscriptWriteError` | `TranscriptStore` | Log warning, continue run | None | Best-effort partial write |

---

## Consequences

- `engine.py` follows the per-turn sequence as a single ordered function. Branching for repair and escalation is explicit.
- Energy is always deducted before the turn file is written. The transcript always reflects true remaining energy.
- Repair attempts are part of the same turn record, not separate turns. Turn count reflects agent turns, not model calls.
- `run.json` is always up to date after each turn. A partial transcript from a crashed run is valid and inspectable.
- `external_input` is an internal engine concept. It never appears in `AgentTurnResult` output and does not need to be validated as a handoff type.
- `return` and `continue` are routed identically by the engine. Their semantic difference is recorded in the transcript only.
- A run that ends with a valid `final` handoff is always `completed`, regardless of remaining energy.
