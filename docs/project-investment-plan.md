# HydraClaim Project Investment Plan

> For agentic workers: use task-by-task implementation with a focused test cycle after each task. Keep all work local.

**Goal:** A user can save one Source Event, see the exact processing state, recover an abandoned Extraction, control the stored source text, and reproduce the evidence from a clean local HydraDB graph.

**Architecture:** Keep HydraDB as the only system of record. Preserve the current relations from Source Event to Extraction, from Extraction to Claim, and from Claim through Evidence to Source. Add explicit lease, lifecycle, reset, fixture, and proof boundaries around the existing pipeline.

**Tech Stack:** Python 3.11+, HydraDB OpenCypher query API, Docker Compose, argparse, pytest, and local Git.

**Spec:** `CONTEXT.md` and `docs/superpowers/specs/2026-08-21-durable-source-capture-design.md`.

## Global Constraints

- Use the exact domain terms Claim, Subject, Predicate, Evidence, Source, Source Event, Extraction, Failure Record, Provenance, Supersession, Conflict, Abstention, Probe, and Route.
- HydraClaim is `LOCAL_ONLY`: do not fetch, pull, push, publish, open remote URLs, call GitHub, change remote configuration, or require remote judging.
- HydraDB remains the only system of record.
- Validate complete input before the first write.
- Stop dependent work after a failure. Return an explicit error with inputs, step, state, exception type, message, and full traceback.
- Do not retry silently, select another provider, use cached data, or guess a value.
- Keep existing claim reconciliation, routing, answer, and abstention behavior unchanged unless a test proves that the change is required for the new evidence boundary.
- Keep resets and deletion explicit, scoped, locally guarded, and recoverable by export before mutation.
- Do not read, print, or commit secrets.

## Why

HydraClaim already saves accepted source text before Extraction and records failed processing. This gives a user an inspectable boundary when an Extraction fails. The remaining user problem is that a crashed process can leave a `RUNNING` Extraction without a recovery decision, stored source text has no bounded export or deletion operation, local reset proof is informal, and the current Slack fixture does not prove the full Source Event path.

The target user experience is direct:

1. The user records one Source Event and receives a stable key.
2. The user processes it and sees `CAPTURED`, `PROCESSED`, or `FAILED` with the relevant Extraction and Failure Record.
3. The user can identify an abandoned `RUNNING` Extraction and recover it with an explicit local command. Recovery leaves an audit record.
4. The user can export the accepted source and its Provenance before a bounded deletion. Deletion either completes its declared scope or fails with a clear dependency error.
5. The user can reset a local graph, ingest a sanitized third-party fixture, and reproduce the same evidence without a remote service.

## Observable Success Criteria

- `record` returns one stable Source Event key and `CAPTURED`.
- A successful process shows one Extraction with `SUCCEEDED`, one or more produced Claims, and Provenance back to the exact source text.
- An Extraction failure shows its step and complete Failure Record. No later step runs.
- A stale `RUNNING` Extraction is listed with its lease data. Explicit recovery creates a Failure Record with the recovery cause and marks the dependent state consistently. A non-stale Extraction cannot be recovered.
- Export returns a deterministic JSON document containing the exact Source Event, all bounded Extractions, Failure Records, produced Claim keys, and Provenance identifiers.
- Deletion requires an explicit confirmation and an event key. It removes only the declared event-owned records, or fails before mutation when a shared Claim or other dependency makes the request unsafe.
- A local reset command proves that the target graph is empty before fixture ingestion and proves the expected Source Event, Extraction, Claim, Evidence, and Source counts after ingestion.
- A sanitized Slack export fixture reaches the Source Event path and preserves author, source kind, occurrence time, exact content, and event key determinism. No network call is required.
- A local proof run records the commands, commit identifier, test result, reset result, fixture result, failure result, recovery result, export result, and deletion result. The proof contains no source secrets.

## Non-Goals

- No new database, vector retrieval, passive transcript capture, or remote integration.
- No new Claim predicates or changes to Route selection.
- No automatic retry, provider fallback, or hidden background worker.
- No broad graph deletion command.
- No claim deduplication or reconciliation redesign.
- No claim deletion based only on a Subject or Predicate. The first lifecycle boundary is one explicitly selected Source Event.
- No public deployment or portfolio publication during this judging phase.

## Exact Evidence Gaps

| Gap | Current local evidence | User and portfolio impact |
| --- | --- | --- |
| Stale lease recovery | `hydraclaim/source_events.py:98-147` creates an Extraction with `started_at`, blank `finished_at`, and `RUNNING`. There is no lease owner, lease expiry, heartbeat, stale query, or recovery operation. `hydraclaim/source_event_read.py:28-66` only reads the existing state. | A process crash can leave work without an explicit next state. The recovery claim is not yet independently verifiable. |
| Deletion and export | `hydraclaim/source_event_read.py:28-66` returns exact source content and related records, but `hydraclaim/events.py:1-36` exposes only list and show. There is no event export or deletion operation. | Users cannot exercise a bounded data-lifecycle request over the source text that HydraClaim stores. |
| Local reset evidence | `hydraclaim/benchmark.py:111-112` resets only a query counter. `hydraclaim/schema.py:200-227` deletes only probe nodes. `docs/tasks/T3-demo-polish.md:19-21` describes a demo-only whole-graph delete, but no supported local proof command implements it. | A benchmark or demo can appear repeatable without proving the graph was clean before ingestion. |
| Third-party fixture evidence | `hydraclaim/claims.py:43` accepts `slack`, `linear`, `meeting`, and `chat`; `hydraclaim/slack_import.py:58-108` parses Slack-shaped input; tests cover conversion only. There is no end-to-end sanitized third-party fixture that proves Source Event capture, failure inspection, and Provenance together. | The external-input claim is narrower than the current product surface and cannot yet support a strong portfolio proof. |
| Local proof packet | The repository has 417 passing tests, but no single local run records reset, fixture ingestion, failure, recovery, export, deletion, and Git evidence together. | Reviewers must infer the system behavior from separate tests and documents. |

## Ordered Investment Task Groups

### Task Group 1: Recover abandoned Extractions

**Files:**

- Modify: `hydraclaim/source_events.py`
- Modify: `hydraclaim/source_event_read.py`
- Modify: `hydraclaim/process.py`
- Modify: `hydraclaim/schema.cypher` and `hydraclaim/schema.py` to verify the lease properties and recovery cleanup query
- Test: `tests/test_source_events.py` and `tests/test_source_event_read.py`
- Create: `tests/test_process.py`

**Contract:** `start_extraction(event_key, provider, model, prompt_version, *, lease_owner, lease_seconds, reprocess=False, now=None)` records an explicit lease owner and expiry. `recover_stale_extraction(extraction_key, *, now)` verifies that the Extraction is still `RUNNING` and past its lease, then writes a Failure Record with complete recovery context and updates the related Source Event. A non-stale or completed Extraction returns an explicit error before any mutation. A later explicit process request creates a new numbered Extraction and preserves the prior attempt.

- [ ] Add failing adapter tests for lease creation, stable time injection, stale detection, non-stale refusal, recovery idempotency, preserved Failure Record, event status, and reprocess behavior.
- [ ] Implement the lease and recovery contract with explicit errors and no retry.
- [ ] Run `python -m pytest tests/test_source_events.py tests/test_source_event_read.py tests/test_process.py -q` and confirm all focused tests pass.

### Task Group 2: Add bounded export and deletion

**Files:**

- Modify: `hydraclaim/source_event_read.py`
- Modify: `hydraclaim/source_events.py`
- Modify: `hydraclaim/events.py` and `hydraclaim/cli.py`
- Test: `tests/test_source_event_read.py`, `tests/test_source_events.py`, and the event CLI tests
- Document: `README.md` and `docs/DEMO.md` after the command contract is tested

**Contract:** Add `export_event(db, event_key) -> dict` for deterministic JSON containing the accepted Source Event, bounded Extractions, Failure Records, produced Claim keys, and Provenance identifiers. Add `delete_event(db, event_key, *, confirmed: bool) -> dict`. Before mutation, it must detect shared Claims or other undeclared relations and return an error with the exact dependency. It must never delete unrelated graph data or report success after a partial write. Export must be the recommended local audit step before deletion.

- [ ] Add failing tests for missing keys, missing confirmation, shared-Claim refusal, complete owned-record deletion, repeated deletion, exact content preservation in export, stable ordering, and failure logging with full context.
- [ ] Implement the event-scoped read and mutation operations with dependency checks before writes.
- [ ] Use a local HydraDB graph to verify no dangling relation remains within the declared scope, then run the full event and CLI test set.

### Task Group 3: Prove clean local reset and repeatable evidence

**Files:**

- Create: `scripts/verify-local-evidence.ps1`
- Create: `scripts/reset-local-graph.ps1`
- Test: `tests/test_local_evidence.py` for command construction and failure conditions
- Document: `docs/DEMO.md` and the project investment proof section below

**Contract:** A local-only reset requires an explicit confirmation value, verifies the HydraDB endpoint is loopback or the configured local Docker service, exports the selected evidence first, deletes the local graph, and verifies zero application nodes before the next step. Any missing Docker service, non-local endpoint, failed query, or non-zero count stops the run. There is no fallback to an existing graph.

- [ ] Add tests for the explicit confirmation, loopback endpoint guard, zero-node check, fixture count check, and fail-loud behavior.
- [ ] Implement the reset and proof commands with no fallback to an existing graph.
- [ ] Run the reset against the local Docker Compose HydraDB, verify zero application nodes, ingest the fixture, verify expected counts, and run the failure and recovery cases. Keep generated output outside tracked source files. Run `git diff --check` and the complete test suite.

### Task Group 4: Add a sanitized third-party fixture

**Files:**

- Create: `data/fixtures/slack/source-event-recovery.json`
- Create: `tests/test_third_party_fixture.py`
- Modify: `scripts/verify-local-evidence.ps1` to consume the fixture
- Document: `docs/DEMO.md`

**Contract:** The fixture uses the existing Slack export shape and contains a small sequence that creates one accepted Source Event, one successful or failed Extraction, and one Provenance path. It contains synthetic names and text only. The test proves conversion, deterministic event keys, exact source content, explicit failure state, and no network call. It does not claim live Slack integration.

- [ ] Add the sanitized Slack fixture and offline parser and pipeline tests.
- [ ] Run the fixture through the local reset proof and inspect the event with `events show`.
- [ ] Confirm that a fixture failure stops dependent work and leaves the Source Event available.

### Task Group 5: Produce the local portfolio proof

**Files:**

- Create: `docs/project-investment-proof.md`
- Modify: `README.md` only to link the local proof and state the `LOCAL_ONLY` boundary
- Modify: `scripts/verify-local-evidence.ps1` to emit the proof inputs and result summary

**Contract:** The proof document records the user outcome, exact local commands, current commit identifier, test count, clean reset result, fixture result, successful Provenance result, explicit Failure Record result, stale-lease recovery result, export result, deletion result, and known limits. It uses exact HydraClaim domain terms and does not contain remote links or secret values.

- [ ] Generate the proof document from local command output and the current commit identifier.
- [ ] Run `python -m pytest -q`, the local evidence script, and `git diff --check` from a clean local working tree.
- [ ] Review every claim in the proof against command output or a test.

## Local Verification Matrix

| Check | Command or evidence | Required result |
| --- | --- | --- |
| Unit and integration tests | `python -m pytest -q` | All tests pass. Current baseline: 417 passed. |
| Formatting and lint | `ruff check --fix .` then `ruff format .` | No unresolved lint or format error. Run only after product implementation changes. |
| Query dialect | `python -m hydraclaim.schema --verify` against local HydraDB | Every probe passes and cleanup reports probe nodes deleted. |
| Reset | Local reset script with explicit confirmation | The graph is empty before fixture ingestion. |
| Fixture | Local evidence script with the sanitized Slack fixture | Source Event, Extraction, Failure Record, Claim, Evidence, Source, and Provenance checks match the declared case. |
| Failure policy | Focused extraction, reconciliation, write, and recovery tests | The first failed step stops dependent work and records full context. |
| Source lifecycle | Export, dependency refusal, deletion, and post-delete queries | The command either completes its declared scope or reports an explicit error before mutation. |
| Local Git proof | `git status --short`, `git log --oneline -1`, `git diff --check` | No remote operation is needed. No product-code change is hidden. |

## Portfolio Proof

The strongest hiring signal is one reproducible local session that shows a real boundary, not a feature list. The proof should show the user recording a Source Event, inspecting exact content, seeing an Extraction fail with a Failure Record, recovering an abandoned lease, and answering only from supported Claims. It should then show export before deletion, explicit refusal for an unsafe shared dependency, a clean local reset, and the same result from the sanitized Slack fixture.

The proof must state that HydraClaim is `LOCAL_ONLY`. It must show the current local commit identifier and test output. It must not use a hosted demo, GitHub, a remote Git command, live Slack, or unverified performance numbers.

## Risks and Controls

- Lease expiry can be wrong if clocks differ. Use one injected UTC time source for tests and record the lease values used in the decision.
- Recovery can hide a real running process. Require an explicit stale threshold, owner, and current-time check. Refuse recovery while the lease is valid.
- Deletion can remove shared data. Inspect relations before mutation, refuse shared Claims, and require export plus confirmation.
- HydraDB query support can differ from a normal Cypher server. Add each new statement to the local schema verification and fail the proof if it is unsupported.
- A local reset can destroy evidence. Require a local endpoint guard and explicit confirmation. Export the selected event before deletion.
- A fixture can be mistaken for a live integration. Label it as a sanitized Slack export and test the boundary without network access.
- Logs can expose source text. Keep source text in the event inspection output, not failure logs, and test that logs contain context without raw content.

## First Bounded Local Implementation Task

Implement Task Group 1, lease metadata and stale-`RUNNING` recovery, in one local change. Start with failing adapter tests for a `RUNNING` Extraction whose lease has expired, a valid lease, a completed Extraction, and a second explicit attempt. Then add the smallest state transition that writes a Failure Record with the event key, Extraction key, lease timestamps, recovery time, and full error context. Do not add export, deletion, reset, fixtures, or remote operations in this task. The task is complete only when the focused tests and the full `python -m pytest -q` run pass.
