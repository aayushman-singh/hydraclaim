# HydraClaim Core Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make claim reads bounded, centralize graph writes, return structured answers, and stop all silent failure behavior.

**Architecture:** One claim-read module owns scoped queries, probes, route selection, and answer results. One graph-write module owns HydraDB identifiers, query text, write order, and idempotency. The existing `HydraDB.query` interface remains the external-system seam.

**Tech Stack:** Python 3.11+, dataclasses, argparse, httpx, pytest, Ruff, HydraDB Cypher subset.

**Spec:** `docs/superpowers/specs/2026-08-20-hydraclaim-rename-cli-architecture-design.md`

## Global Constraints

- Support Python 3.11, 3.12, and 3.13.
- Use only the verified HydraDB query dialect.
- Keep claim writes idempotent.
- Keep answer creation deterministic after classification.
- Keep abstention as a valid answer result.
- Do not add silent alternative behavior or internal retries.
- Run `ruff check --fix . && ruff format .` before each Python commit.
- Use atomic conventional commits.

---

### Task 1: Explicit classification modes

**Files:**
- Modify: `hydraclaim/router.py`
- Modify: `hydraclaim/retrieve.py`
- Modify: `hydraclaim/ask.py`
- Modify: `hydraclaim/serve.py`
- Test: `tests/test_router.py`
- Test: `tests/test_retrieve.py`
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: `classify(question, roster, *, mode: str, llm_fn=None, now=None) -> Classification`
- Invariant: `mode` is exactly `heuristic` or `llm`; `llm` failures propagate.

- [ ] **Step 1: Write failing mode tests**

```python
def test_llm_mode_propagates_classifier_error():
    def broken(_prompt):
        raise RuntimeError("classifier unavailable")
    with pytest.raises(RuntimeError, match="classifier unavailable"):
        classify("Who owns launch?", ROSTER, mode="llm", llm_fn=broken)

def test_unknown_classification_mode_fails():
    with pytest.raises(ValueError, match="classification mode"):
        classify("Who owns launch?", ROSTER, mode="auto")
```

- [ ] **Step 2: Run the focused tests**

Run: `python -m pytest tests/test_router.py -v`
Expected: FAIL because `classify` does not accept `mode` and it catches the classifier error.

- [ ] **Step 3: Implement explicit selection**

```python
def classify(question, roster, *, mode="heuristic", llm_fn=None, now=None):
    if mode == "heuristic":
        return heuristic_classify(question, roster, now=now)
    if mode != "llm":
        raise ValueError(f"unknown classification mode: {mode!r}")
    if llm_fn is None:
        raise ValueError("llm classification mode requires llm_fn")
    return _classify_with_llm(question, roster, llm_fn=llm_fn, now=now)
```

Remove every broad classifier catch. Pass the explicit mode through `retrieve.answer`, CLI handling, and HTTP configuration.

- [ ] **Step 4: Test both callers**

Run: `python -m pytest tests/test_router.py tests/test_retrieve.py tests/test_serve.py -v`
Expected: PASS. No test expects an automatic mode change.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim/router.py hydraclaim/retrieve.py hydraclaim/ask.py hydraclaim/serve.py tests/test_router.py tests/test_retrieve.py tests/test_serve.py
git commit -m "refactor: make classification mode explicit"
```

### Task 2: Fail on invalid temporal input

**Files:**
- Modify: `hydraclaim/extract.py`
- Modify: `hydraclaim/slack_import.py`
- Test: `tests/test_extract.py`
- Test: `tests/test_slack_import.py`

**Interfaces:**
- Produces: `_reference_date(session) -> date` that raises `ValueError` for invalid timestamps.
- Produces: `_msg_timestamp(message) -> str` that raises `ValueError` for missing or invalid timestamps.

- [ ] **Step 1: Write invalid-input tests**

```python
def test_reference_date_rejects_invalid_timestamp():
    with pytest.raises(ValueError, match="session timestamp"):
        _reference_date({"messages": [{"ts": "not-a-date"}]})

def test_slack_timestamp_rejects_invalid_value():
    with pytest.raises(ValueError, match="Slack timestamp"):
        _msg_timestamp({"ts": "invalid"})
```

- [ ] **Step 2: Confirm the current defaults fail the tests**

Run: `python -m pytest tests/test_extract.py tests/test_slack_import.py -v`
Expected: FAIL because the implementation uses the current date or time.

- [ ] **Step 3: Replace defaults with explicit errors**

```python
try:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
except (AttributeError, TypeError, ValueError) as exc:
    raise ValueError(f"invalid session timestamp: {raw!r}") from exc
```

Use the same pattern for Slack timestamps. Make normalization raise with the predicate and invalid value when a date predicate cannot be parsed.

- [ ] **Step 4: Run temporal tests**

Run: `python -m pytest tests/test_extract.py tests/test_slack_import.py -v`
Expected: PASS with no clock-dependent assertions.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim/extract.py hydraclaim/slack_import.py tests/test_extract.py tests/test_slack_import.py
git commit -m "fix: reject invalid temporal input"
```

### Task 3: Central graph-write module

**Files:**
- Create: `hydraclaim/graph_write.py`
- Modify: `hydraclaim/ingest.py`
- Modify: `hydraclaim/reconcile.py`
- Modify: `hydraclaim/pipeline.py`
- Test: `tests/test_graph_write.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Produces: `GraphWriter(db)` with `ingest_document(document) -> dict` and `apply_plan(plan, scenario_id, entities=None) -> dict`.
- Consumes: an adapter with `query(cypher) -> list[dict]` and `query_one(cypher) -> dict | None`.

- [ ] **Step 1: Write a recording adapter and failing idempotency test**

```python
class RecordingDB:
    def __init__(self):
        self.queries = []
    def query(self, cypher):
        self.queries.append(cypher)
        return []
    def query_one(self, cypher):
        self.queries.append(cypher)
        return {"c": 0}

def test_writer_creates_claims_before_claim_relations(scenario):
    db = RecordingDB()
    GraphWriter(db).ingest_document(scenario)
    claim_index = next(i for i, q in enumerate(db.queries) if ":Claim" in q and "SUPPORTED_BY" in q)
    relation_index = next(i for i, q in enumerate(db.queries) if ":SUPERSEDES" in q)
    assert claim_index < relation_index
```

- [ ] **Step 2: Run the new test**

Run: `python -m pytest tests/test_graph_write.py -v`
Expected: FAIL because `hydraclaim.graph_write` does not exist.

- [ ] **Step 3: Move write knowledge behind `GraphWriter`**

```python
class GraphWriter:
    def __init__(self, db):
        self._db = db

    def ingest_document(self, document: dict) -> dict:
        validate_scenario(document)
        # Create claims first. Create directed relations second.

    def apply_plan(self, plan: dict, scenario_id: str,
                   entities: list[dict] | None = None) -> dict:
        # Validate the complete plan before the first query.
        # Record the deterministic reconciliation decisions.
```

Move `_props`, node checks, relation checks, claim writes, relation writes, identifier conversion, and status closure into this module. Keep `plan_writes` pure in `reconcile.py`. Keep `ingest.ingest_document(db, document)` and `reconcile.apply_plan(db, plan, scenario_id, entities=None)` as public functions. Each function creates `GraphWriter(db)` and calls the applicable method. They contain no write logic.

- [ ] **Step 4: Verify write behavior and private import removal**

Run: `python -m pytest tests/test_graph_write.py tests/test_reconcile.py tests/test_ingest_api.py -v`
Expected: PASS.

Run: `rg -n "from hydraclaim\.ingest import _|from hydraclaim\.reconcile import _" hydraclaim`
Expected: no output.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim/graph_write.py hydraclaim/ingest.py hydraclaim/reconcile.py hydraclaim/pipeline.py tests/test_graph_write.py tests/test_reconcile.py
git commit -m "refactor: centralize graph writes"
```

### Task 4: Bounded claim-read module

**Files:**
- Create: `hydraclaim/claim_read.py`
- Modify: `hydraclaim/probe.py`
- Modify: `hydraclaim/retrieve.py`
- Modify: `hydraclaim/serve.py`
- Modify: `hydraclaim/benchmark.py`
- Test: `tests/test_claim_read.py`
- Test: `tests/test_retrieve.py`
- Test: `tests/test_benchmark.py`

**Interfaces:**
- Produces: frozen `ClaimScope`, `ClaimView`, `ProbeResult`, `Citation`, and `AnswerResult` dataclasses.
- Produces: `ClaimReader(db).read_claims(scope) -> tuple[ClaimView, ...]`.
- Produces: `ClaimReader(db).answer(question, *, classification_mode="heuristic", llm_fn=None, now=None, force_route=None) -> AnswerResult`.

- [ ] **Step 1: Write failing structured-result and query-scope tests**

```python
def test_answer_returns_structured_abstention(fake_db):
    result = ClaimReader(fake_db).answer("Who owns unknown?", now=NOW)
    assert result.route == "ABSTAIN"
    assert result.citations == ()
    assert result.classification is not None

def test_probe_queries_limit_relations_to_selected_claims(recording_db):
    ClaimReader(recording_db).answer("Who owns launch?", now=NOW)
    relation_queries = [q for q in recording_db.queries if "SUPERSEDES" in q or "CONTRADICTS" in q]
    assert relation_queries
    assert all("ABOUT" in q and "e.name" in q for q in relation_queries)
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_claim_read.py -v`
Expected: FAIL because `hydraclaim.claim_read` does not exist.

- [ ] **Step 3: Implement the typed read module**

```python
@dataclass(frozen=True)
class ClaimScope:
    subject: str
    predicate: str | None = None
    active_only: bool = False
    as_of: str | None = None
    limit: int = 25

@dataclass(frozen=True)
class AnswerResult:
    route: str
    text: str
    citations: tuple[Citation, ...]
    classification: Classification
    probe: ProbeResult | None
```

Build relation queries from the same subject and predicate clauses as claim reads. Do not fetch the full `SUPERSEDES` or `CONTRADICTS` relation sets. Move graph-view and benchmark reads through `ClaimReader`.

- [ ] **Step 4: Run all read callers**

Run: `python -m pytest tests/test_claim_read.py tests/test_retrieve.py tests/test_serve.py tests/test_benchmark.py -v`
Expected: PASS. Benchmark abstention checks `result.route`, not a text phrase.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim/claim_read.py hydraclaim/probe.py hydraclaim/retrieve.py hydraclaim/serve.py hydraclaim/benchmark.py tests/test_claim_read.py tests/test_retrieve.py tests/test_serve.py tests/test_benchmark.py
git commit -m "refactor: centralize bounded claim reads"
```

### Task 5: Verified schema source and probe name

**Files:**
- Modify: `hydraclaim/schema.py`
- Modify: `hydraclaim/schema.cypher`
- Test: `tests/test_cypher.py`
- Test: `tests/test_model.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `HydraClaimProbe` as the temporary verification label.
- Invariant: schema examples contain only verified syntax.

- [ ] **Step 1: Write failing schema drift tests**

```python
def test_schema_document_uses_verified_dialect():
    text = Path("hydraclaim/schema.cypher").read_text(encoding="utf-8")
    assert "TGProbe" not in text
    assert "IS NULL" not in text
    assert "length(" not in text
    assert "-[:CONTRADICTS]-" not in text

def test_probe_uses_hydraclaim_label():
    statements = [query for _, queries in _probes("00000001") for query in queries]
    assert all("TGProbe" not in query for query in statements)
    assert any("HydraClaimProbe" in query for query in statements)
```

- [ ] **Step 2: Run schema tests**

Run: `python -m pytest tests/test_schema.py tests/test_cypher.py tests/test_model.py -v`
Expected: FAIL on old labels and unsupported schema text.

- [ ] **Step 3: Replace stale schema examples**

Use `HydraClaimProbe` in all verification statements. Rewrite `schema.cypher` with directed matches, empty-string validity, and client-side chain-depth notes. Make cleanup failure set the verification result to false.

- [ ] **Step 4: Run schema tests**

Run: `python -m pytest tests/test_schema.py tests/test_cypher.py tests/test_model.py -v`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim/schema.py hydraclaim/schema.cypher tests/test_schema.py tests/test_cypher.py tests/test_model.py
git commit -m "fix: align schema with verified HydraDB dialect"
```

### Task 6: Context-rich ingest failures and full regression

**Files:**
- Modify: `hydraclaim/ingest_api.py`
- Modify: `hydraclaim/serve.py`
- Test: `tests/test_ingest_api.py`
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: stable HTTP error codes with concise response text and full server traceback logs.

- [ ] **Step 1: Write a failing log-context test**

```python
def test_ingest_failure_logs_step_and_traceback(caplog, monkeypatch):
    monkeypatch.setattr("hydraclaim.ingest_api.extract_session", raising_extractor)
    status, body = handle_ingest(fake_db, VALID_REQUEST)
    assert status == 500
    assert body["code"] == "ingest_failed"
    assert "step=extract" in caplog.text
    assert "Traceback" in caplog.text
```

- [ ] **Step 2: Confirm the current response fails**

Run: `python -m pytest tests/test_ingest_api.py tests/test_serve.py -v`
Expected: FAIL because responses contain only a raw error string and logs lack traceback context.

- [ ] **Step 3: Add step-local exception context**

```python
logger.exception(
    "ingest failed step=%s scenario=%s input=%r",
    step,
    scenario_id,
    request_summary,
)
return 500, {"code": "ingest_failed", "error": "ingestion failed"}
```

Do not continue after the exception. Do not include secrets or full source documents in logs.

- [ ] **Step 4: Run the full core suite**

Run: `python -m pytest tests/ -v`
Expected: PASS.

Run: `ruff check --fix . && ruff format . && git diff --check`
Expected: no errors and no formatting changes after the final check.

- [ ] **Step 5: Commit**

```bash
git add hydraclaim/ingest_api.py hydraclaim/serve.py tests/test_ingest_api.py tests/test_serve.py
git commit -m "fix: report ingest failures with context"
```
