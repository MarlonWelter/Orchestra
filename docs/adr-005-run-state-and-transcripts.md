# ADR-005: Run State and Transcript Storage

## Status

Accepted

## Context

Orchestra runs are multi-turn workflows involving one or more agents. During early development, it must be easy to inspect what happened, debug failed handoffs, reproduce model calls, and understand how energy was consumed.

A run transcript is therefore not an optional logging feature. It is part of the core runtime design.

## Decision

Store each run as a file-based transcript under `.orchestra/runs/<run_id>/`.

The run directory contains:

```text
.orchestra/runs/<run_id>/
├── run.json
└── turns/
    ├── 001-george.json
    ├── 002-warren.json
    └── 003-george.json
```

`run.json` acts as an index and summary for the run. Each turn file stores the full detail for one completed turn.

For v1, storage is local and file-based. Database-backed storage is deferred.

## Definitions

### Run

A run is one external request processed by one configured agent team.

A run starts when external input is given to the team's `entry_agent` and ends when one of the following happens:

- an agent returns a valid `final` handoff;
- the energy budget is exhausted;
- the engine encounters an unrecoverable error;
- the run is manually stopped.

### Turn

A turn is one completed activation of one agent.

A turn includes:

- the active agent;
- the logical input sent to that agent;
- the assembled model input;
- the model response;
- the parsed `AgentTurnResult`;
- validation results;
- energy before and after;
- token usage;
- timestamps.

Invalid-output repair attempts are logged as part of the turn that triggered them.

## Incremental write policy

Turn files are written incrementally.

Each turn file is written when that turn completes, not at the end of the run. This ensures partial transcripts remain available if the engine crashes, is interrupted, or runs out of energy midway through a workflow.

`run.json` is updated after every completed turn so it remains a useful index even for incomplete runs.

## Run status

`run.json` must include a `status` field.

Allowed values:

| Status | Meaning |
|---|---|
| `running` | Run has started and has not reached a terminal state |
| `completed` | Run ended with a valid final answer |
| `failed` | Run ended because of an unrecoverable error |
| `exhausted` | Run ended because energy reached zero |

Future statuses may be added later, such as `stopped` or `paused`, but v1 only requires the four values above.

## run.json format

Example:

```json
{
  "run_id": "run_2026-06-06_140501_ab12",
  "team_id": "investment_team",
  "status": "completed",
  "external_input": "Analyze Alphabet and decide whether to add to the position.",
  "entry_agent": "george",
  "started_at": "2026-06-06T14:05:01Z",
  "completed_at": "2026-06-06T14:08:32Z",
  "energy": {
    "initial": 20,
    "used": 3,
    "remaining": 17
  },
  "turns": [
    {
      "turn_id": "001-george",
      "index": 1,
      "agent_id": "george",
      "status": "completed",
      "handoff_type": "continue",
      "recipient": "warren",
      "path": "turns/001-george.json"
    },
    {
      "turn_id": "002-warren",
      "index": 2,
      "agent_id": "warren",
      "status": "completed",
      "handoff_type": "return",
      "recipient": "george",
      "path": "turns/002-warren.json"
    },
    {
      "turn_id": "003-george",
      "index": 3,
      "agent_id": "george",
      "status": "completed",
      "handoff_type": "final",
      "recipient": "external",
      "path": "turns/003-george.json"
    }
  ],
  "final_answer": "Alphabet remains a high-quality long-term holding, but the team recommends staged buying rather than a full immediate increase.",
  "error": null
}
```

## run.json responsibilities

`run.json` is not the full transcript. It is an index and summary.

It should contain:

- run id;
- team id;
- status;
- external input;
- entry agent;
- start and end timestamps;
- energy summary;
- ordered turn index;
- final answer if completed;
- error summary if failed.

A reader should be able to understand the high-level shape and outcome of a run without opening every turn file.

## Turn file format

Example:

```json
{
  "turn_id": "002-warren",
  "run_id": "run_2026-06-06_140501_ab12",
  "index": 2,
  "agent_id": "warren",
  "sender_agent_id": "george",
  "status": "completed",
  "started_at": "2026-06-06T14:05:40Z",
  "completed_at": "2026-06-06T14:06:21Z",
  "energy": {
    "before": 19,
    "cost": 1,
    "after": 18
  },
  "logical_input": {
    "sender_agent_id": "george",
    "task": "Analyze Alphabet from the perspective of a conservative long-term quality investor.",
    "handoff_type": "continue",
    "energy_before": 19
  },
  "assembled_input": {
    "model_profile": "frontier",
    "model": "anthropic/claude-sonnet-4-6",
    "messages": [
      {
        "role": "system",
        "content": "... global Orchestra system prompt ..."
      },
      {
        "role": "system",
        "content": "... Warren role prompt ..."
      },
      {
        "role": "user",
        "content": "... current input assembled by the engine ..."
      }
    ]
  },
  "model_response": {
    "text": "{ ... raw assistant text ... }",
    "usage": {
      "input_tokens": 3200,
      "output_tokens": 850,
      "total_tokens": 4050
    },
    "finish_reason": "stop"
  },
  "parsed_result": {
    "agent_id": "warren",
    "response": "Alphabet is a high-quality business, but margin of safety depends on assumptions about AI monetization and regulatory risk.",
    "handoff": {
      "type": "return",
      "recipient": "george",
      "task": "Use this conservative assessment in the final synthesis."
    },
    "notes": [
      "Regulatory pressure is a material uncertainty."
    ]
  },
  "validation": {
    "valid": true,
    "errors": []
  },
  "repair_attempts": []
}
```

## Two-layer turn input

Each turn records input at two levels.

### Logical input

The logical input is human-readable and describes why the agent was called.

It includes:

- sender agent;
- handoff type;
- task string from the previous handoff;
- energy before the turn;
- optional summary of relevant prior context.

This is the layer humans should read first.

### Assembled input

The assembled input is the exact input passed to the model client.

It includes:

- resolved model profile;
- resolved model string;
- full messages list;
- tool schemas if used;
- response schema if used.

This layer enables exact replay, debugging, and prompt inspection.

## Full prompt storage

For v1, full assembled prompts/messages should be stored.

Reasoning:

- debugging prompt construction is central to early development;
- failed handoffs are otherwise hard to diagnose;
- exact replay requires full model input;
- prompt evolution can be compared across runs.

This has privacy and secret-leakage implications. The engine should avoid placing secrets in prompts, and later versions may add redaction. For v1, debuggability is prioritized.

## Energy accounting

For v1:

- each completed model call costs `1` energy;
- invalid-output repair attempts also cost `1` energy;
- token usage is recorded but does not affect energy cost;
- when energy reaches `0`, the run status becomes `exhausted`.

`run.json` stores the run-level energy summary. Each turn file stores turn-level energy before, cost, and after.

## Failure handling

If a turn fails before producing a valid `AgentTurnResult`, the engine should still write a turn file if possible.

The failed turn file should include:

- logical input;
- assembled input if available;
- raw model response if available;
- validation errors or exception details;
- repair attempts;
- energy consumed;
- failure status.

`run.json` should then be updated to `failed` unless the engine can recover by handing control to a permitted manager agent.

## Resume behavior

True resume is deferred.

The v1 transcript format should make resume possible later, but the first implementation only needs to support inspection and debugging.

## Non-goals

This ADR does not define:

- database storage;
- remote run storage;
- UI visualization;
- run replay execution;
- resume semantics;
- secret redaction;
- long-term archival format.

These can be added after the basic sequential engine works reliably.

## Consequences

- File-based transcripts become part of the core engine, not optional debug output.
- Run and turn IDs must be stable and deterministic enough for inspection.
- The engine must write transcripts incrementally.
- Full prompts are stored in v1, so users should treat `.orchestra/runs/` as potentially sensitive.
- Later tooling can build on `run.json` without reading every turn file.
