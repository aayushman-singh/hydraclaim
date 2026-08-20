# Durable source capture design

## User result

### Why

HydraClaim can create claims from a supplied document. It does not first confirm that it saved the source record. If extraction stops, the user cannot inspect one durable record of what HydraClaim accepted, what step stopped, or which extraction created a claim.

The user must see a clear boundary: HydraClaim either rejects a source event before acceptance, or saves it and reports its exact processing state.

### Success criteria

1. A user records one source event and receives its stable identifier.
2. An accepted source event remains available when extraction or graph writing stops.
3. A user sees whether each event is `CAPTURED`, `PROCESSED`, or `FAILED`.
4. A failed event shows the failed step, error type, message, and traceback reference.
5. A user opens a source event and sees the exact accepted text and source information.
6. A user opens a claim and sees the extraction that produced it and the source event that supports its evidence.
7. Repeated capture of the same source event creates no duplicate event.
8. HydraClaim performs no automatic retry and selects no alternative processing path.

### Non-goals

- Do not add Model Context Protocol (MCP) tools.
- Do not add passive transcript capture.
- Do not add a second database.
- Do not add vector retrieval.
- Do not change claim reconciliation, routing, or answer behavior.
- Do not add raw Cypher access.
- Do not process several independent source events as one hidden unit.

### Hard constraints

- HydraDB remains the only system of record.
- HydraClaim validates the complete source event before the first write.
- Capture success means that the complete source event is durable.
- Processing failure does not remove or change accepted source text.
- A failed step stops all dependent work.
- Every error includes the source event identifier and processing step.
- Existing scenario and pipeline commands remain available.
- Existing claim, evidence, source, conflict, supersession, and abstention behavior remains unchanged.

## System relations

```text
environment
  -> record source event
  -> SourceEvent

SourceEvent
  -> explicit process request
  -> Extraction
  -> reconciliation plan
  -> Claim

Claim
  -> Evidence
  -> SourceEvent
  -> Source

SourceEvent or Extraction failure
  -> FailureRecord
  -> status output
```

The environment sends one source event to HydraClaim. HydraClaim validates the full event. It then creates one `SourceEvent` node. This write is the acceptance boundary.

An explicit process operation reads one accepted event. It creates one `Extraction` node before it calls the language model. The extraction records the provider, model, prompt version, start time, and final state. A successful extraction produces claims through the existing reconciliation and graph-write path.

Evidence links to the exact source event. The existing `Source` node continues to identify the author and source kind. `SourceEvent` stores the accepted occurrence. These nodes have different purposes and cannot be combined.

## Nodes

### SourceEvent

`SourceEvent` stores one accepted input occurrence.

Required properties:

- `id`: deterministic integer graph identifier.
- `key`: stable external identifier.
- `source_kind`: controlled source kind.
- `author`: non-empty author name.
- `occurred_at`: validated International Organization for Standardization (ISO) timestamp.
- `captured_at`: HydraClaim acceptance timestamp.
- `content`: exact accepted text.
- `content_hash`: hash of source identity and content.
- `status`: `CAPTURED`, `PROCESSED`, or `FAILED`.

The event key uses a supplied stable source identifier. If the caller has no source identifier, HydraClaim derives the key from the source kind, author, occurrence time, and content hash. The same input produces the same key.

### Extraction

`Extraction` records one explicit attempt to process one source event.

Required properties:

- `id`: deterministic integer graph identifier.
- `key`: source event key plus attempt number.
- `provider`: selected language-model provider.
- `model`: selected model identifier.
- `prompt_version`: exact extraction prompt version.
- `started_at`: attempt start timestamp.
- `finished_at`: completion timestamp or an empty string while active.
- `status`: `RUNNING`, `SUCCEEDED`, or `FAILED`.

An explicit second process request creates a new extraction attempt. It does not change the previous extraction record.

### FailureRecord

`FailureRecord` stores one stopped processing step.

Required properties:

- `id`: deterministic integer graph identifier.
- `step`: `EXTRACT`, `RECONCILE`, or `WRITE`.
- `error_type`: exception type.
- `message`: concise error message.
- `traceback`: complete traceback text.
- `failed_at`: failure timestamp.

HydraClaim links the failure to the extraction. It sets the extraction and source event states to `FAILED`. It then returns a nonzero command result. It does not continue.

## Directed relations

- `(Extraction)-[:READ_FROM]->(SourceEvent)` identifies the accepted input.
- `(Claim)-[:PRODUCED_BY]->(Extraction)` identifies the process that created the claim.
- `(Evidence)-[:QUOTED_FROM]->(SourceEvent)` identifies the exact source occurrence.
- `(SourceEvent)-[:FROM]->(Source)` identifies the author and source kind.
- `(Extraction)-[:FAILED_WITH]->(FailureRecord)` identifies a stopped step.

The existing `(Claim)-[:SUPPORTED_BY]->(Evidence)` relation remains unchanged. The existing `(Evidence)-[:FROM]->(Source)` relation remains during migration. New writes use the source-event path. A later migration can remove the duplicate direct relation only after all consumers use `QUOTED_FROM`.

## Public commands

### Record

```text
hydraclaim record SOURCE_JSON
```

The command validates and captures one event. It prints the event key and `CAPTURED`. It does not start extraction.

### Process

```text
hydraclaim process EVENT_KEY
```

The command processes one captured or failed event through extraction, reconciliation, and graph writing. It reports the extraction key and final state. A failed attempt returns a nonzero exit code.

### Status

```text
hydraclaim status
```

The command prints counts for `CAPTURED`, `PROCESSED`, and `FAILED` events. It prints each failed event key and failed step. It does not hide failed records in a total count.

### Event inspection

```text
hydraclaim events list
hydraclaim events show EVENT_KEY
```

The list command returns bounded, newest-first event summaries. The show command returns the exact accepted event, extraction attempts, failure records, and produced claim keys.

## Existing command adaptation

The current `pipeline` command remains a convenience command. For each validated message, it calls the same capture operation and then the same process operation. It prints each accepted event key before processing starts.

If one event fails, `pipeline` stops. It reports the accepted event key and failed step. The user can inspect that event and explicitly run `process` again after correction.

Oracle scenario ingestion does not create extraction records because no extraction occurs. It creates source events for supplied evidence and marks them `PROCESSED` with an explicit `ingestion_kind` of `ORACLE`.

## Failure behavior

- Invalid input creates no node.
- Capture write failure creates no success result.
- Extraction failure creates one failed extraction and one failure record.
- Reconciliation failure creates one failed extraction and one failure record.
- Claim write failure creates one failed extraction and one failure record. Existing accepted source data remains.
- An event remains `CAPTURED` while no process request exists.
- HydraClaim never changes `FAILED` to `PROCESSED`. A new successful extraction causes the source event to become `PROCESSED` and preserves all failed attempts.
- A process request for an already processed event fails unless the user supplies an explicit reprocess option.

## Read boundaries

One source-event read module owns all query text for events, extractions, and failure records. CLI commands and the later MCP interface call this module. They do not define Cypher.

All list operations require an explicit limit and use stable ordering. Event reads select one event identifier before they read related nodes. Query cost does not grow with unrelated graph data.

## Verification

- Test full validation before capture writes.
- Test deterministic event identifiers and repeated capture.
- Test exact source-text preservation.
- Test each state change.
- Test failure records for extraction, reconciliation, and graph-write steps.
- Test that dependent work stops after each failure.
- Test that an explicit second attempt preserves the first attempt.
- Test bounded event lists with unrelated graph data.
- Test event inspection provenance from claim to extraction to source event.
- Test existing pipeline behavior through the new operations.
- Test oracle ingestion without a false language-model extraction record.
- Test every new command, help text, error code, and exit status.

## MCP boundary

The later MCP design uses two operations: one record operation and one answer operation. The record operation calls the capture interface defined here. It does not call graph-write functions directly. The answer operation calls the existing claim-read interface. This design adds no MCP dependency and makes no decision about passive capture.
