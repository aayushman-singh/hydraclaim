# Durable Source Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store every accepted source event before extraction and let users inspect its processing state and provenance.

**Architecture:** A new source-event module owns validation, HydraDB query text, state changes, extraction attempts, failures, and bounded reads. Existing pipeline and graph-write modules call this interface. CLI modules expose record, process, status, and event inspection without direct Cypher.

**Tech Stack:** Python 3.11-3.13, HydraDB OpenCypher subset, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-durable-source-capture-design.md`

## Global Constraints

- HydraDB remains the only system of record.
- Validate a complete source event before the first write.
- Stop dependent work after each failure.
- Do not retry or select another processing path.
- Preserve existing claim and answer behavior.
- Use the verified HydraDB query dialect.
- Keep all reads bounded and scoped by selected identifiers.

---

### Task 1: Source event write contract

**Files:**
- Create: `hydraclaim/source_events.py`
- Create: `tests/test_source_events.py`
- Modify: `hydraclaim/model.py`

**Interfaces:**
- Produces: `validate_source_event(value: object) -> dict`
- Produces: `source_event_key(event: dict) -> str`
- Produces: `SourceEventStore.capture(event: dict, *, ingestion_kind: str = "EXTRACTED") -> dict`

- [ ] Write tests for complete validation, exact content, stable keys, and duplicate capture.
- [ ] Run `python -m pytest tests/test_source_events.py -q` and confirm the tests fail because the module does not exist.
- [ ] Add deterministic source-event properties and the minimal capture queries.
- [ ] Run `python -m pytest tests/test_source_events.py -q` and confirm all tests pass.
- [ ] Commit with `feat: add durable source event capture`.

### Task 2: Processing attempts and explicit failures

**Files:**
- Modify: `hydraclaim/source_events.py`
- Modify: `tests/test_source_events.py`

**Interfaces:**
- Produces: `SourceEventStore.start_extraction(event_key, provider, model, prompt_version, *, reprocess=False) -> dict`
- Produces: `SourceEventStore.complete_extraction(extraction_key, claim_keys) -> dict`
- Produces: `SourceEventStore.fail_extraction(extraction_key, step, exc) -> dict`

- [ ] Write tests for attempt numbering, processed-event rejection, preserved failed attempts, state changes, and traceback records.
- [ ] Run the focused tests and confirm failure for missing methods.
- [ ] Implement scoped attempt and failure writes with no retry.
- [ ] Run focused and full source-event tests.
- [ ] Commit with `feat: record extraction attempts and failures`.

### Task 3: Bounded audit reads

**Files:**
- Create: `hydraclaim/source_event_read.py`
- Create: `tests/test_source_event_read.py`

**Interfaces:**
- Produces: `list_events(db, *, limit: int = 20) -> list[dict]`
- Produces: `read_event(db, event_key: str) -> dict`
- Produces: `event_status(db) -> dict`

- [ ] Write recording-adapter tests for stable order, explicit limits, selected identifiers, failure details, extraction attempts, and produced claim keys.
- [ ] Run the focused tests and confirm failure for the missing module.
- [ ] Implement the three bounded reads.
- [ ] Run focused tests and confirm all pass.
- [ ] Commit with `feat: add source event audit reads`.

### Task 4: Public audit commands

**Files:**
- Create: `hydraclaim/record.py`
- Create: `hydraclaim/process.py`
- Create: `hydraclaim/status.py`
- Create: `hydraclaim/events.py`
- Modify: `hydraclaim/cli.py`
- Create: `tests/test_source_event_cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_modules.py`

**Interfaces:**
- Produces commands: `record`, `process`, `status`, and `events list|show`.

- [ ] Write CLI tests for help, JSON input, output records, error codes, limits, and explicit reprocess.
- [ ] Run focused tests and confirm command registration failures.
- [ ] Implement command modules through source-event interfaces only.
- [ ] Run all CLI tests.
- [ ] Commit with `feat: add source event commands`.

### Task 5: Pipeline and provenance integration

**Files:**
- Modify: `hydraclaim/pipeline.py`
- Modify: `hydraclaim/graph_write.py`
- Modify: `hydraclaim/model.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_graph_write.py`

**Interfaces:**
- Pipeline captures each message before extraction.
- `GraphWriter.apply_plan(..., extraction_key: str | None = None, source_event_keys: dict[str, str] | None = None)` records `PRODUCED_BY` and `QUOTED_FROM` relations.

- [ ] Write tests that require capture before extraction and require pipeline stop after extraction, reconciliation, or write failure.
- [ ] Write graph-write tests for extraction and source-event provenance relations.
- [ ] Run focused tests and confirm the new expectations fail.
- [ ] Adapt pipeline and graph writing through the new contracts.
- [ ] Run pipeline and graph-write tests.
- [ ] Commit with `feat: preserve pipeline source provenance`.

### Task 6: Oracle ingestion and schema reference

**Files:**
- Modify: `hydraclaim/graph_write.py`
- Modify: `hydraclaim/schema.cypher`
- Modify: `tests/test_graph_write.py`
- Modify: `tests/test_schema.py`

**Interfaces:**
- Oracle ingestion creates processed `SourceEvent` nodes with `ingestion_kind=ORACLE` and no `Extraction` node.

- [ ] Write tests for oracle source events and the absence of false extraction records.
- [ ] Run focused tests and confirm failure.
- [ ] Add oracle capture through the shared source-event store and update the schema reference.
- [ ] Run focused tests.
- [ ] Commit with `feat: preserve oracle source events`.

### Task 7: Documentation and release verification

**Files:**
- Modify: `README.md`
- Modify: `CONTEXT.md`
- Modify: `pyproject.toml`
- Modify: `tests/test_package_metadata.py`

**Interfaces:**
- Documents the four commands, state meanings, explicit processing, and MCP-ready boundary.
- Releases version `0.3.0` only after full verification.

- [ ] Write metadata and help tests for version `0.3.0` and packaged modules.
- [ ] Run focused tests and confirm version failure.
- [ ] Update documentation, domain terms, and package version.
- [ ] Run `ruff check --fix .` and `ruff format .`.
- [ ] Run `python -m pytest tests/ -q`.
- [ ] Run `python -m build` and `python -m twine check dist/*`.
- [ ] Commit with `docs: document durable source capture`.
