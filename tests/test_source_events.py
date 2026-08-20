from __future__ import annotations

import pytest

from hydraclaim.source_events import SourceEventStore, source_event_key


class CaptureDB:
    def __init__(self, *, exists: bool = False):
        self.exists = exists
        self.queries: list[str] = []

    def query_one(self, cypher: str):
        self.queries.append(cypher)
        return {"c": int(self.exists)}

    def query(self, cypher: str):
        self.queries.append(cypher)
        self.exists = True
        return []


class AttemptDB(CaptureDB):
    def __init__(self, event_status="CAPTURED", attempt_count=0):
        super().__init__()
        self.event_status = event_status
        self.attempt_count = attempt_count

    def query_one(self, cypher: str):
        self.queries.append(cypher)
        if "RETURN event.status AS status" in cypher:
            return {"status": self.event_status}
        if "RETURN count(*) AS c" in cypher and "Extraction" in cypher:
            return {"c": self.attempt_count}
        if "RETURN extraction.status AS status" in cypher:
            return {
                "status": "RUNNING",
                "event_id": 101,
                "event_key": "source-event:slack:message-42",
            }
        return {"c": 0}


def event(**overrides):
    value = {
        "source_kind": "slack",
        "author": "Asha Rao",
        "occurred_at": "2026-08-20T10:30:00+00:00",
        "content": "Launch moves to Monday.",
        "source_id": "message-42",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", "unknown"),
        ("author", ""),
        ("occurred_at", "not-a-time"),
        ("content", ""),
        ("source_id", 42),
    ],
)
def test_capture_validates_complete_event_before_any_query(field, value):
    db = CaptureDB()
    invalid = event(**{field: value})

    with pytest.raises(ValueError, match=field):
        SourceEventStore(db).capture(invalid)

    assert db.queries == []


def test_source_event_key_is_stable_and_uses_source_identifier():
    assert source_event_key(event()) == "source-event:slack:message-42"
    assert source_event_key(event()) == source_event_key(event())


def test_source_event_key_is_stable_without_source_identifier():
    first = event()
    first.pop("source_id")
    second = dict(first)

    assert source_event_key(first) == source_event_key(second)
    assert source_event_key(first).startswith("source-event:slack:")


def test_capture_writes_exact_content_and_returns_state():
    db = CaptureDB()

    result = SourceEventStore(db).capture(event())

    assert result == {
        "event_key": "source-event:slack:message-42",
        "status": "CAPTURED",
        "created": True,
    }
    write = db.queries[-1]
    assert "CREATE (event:SourceEvent" in write
    assert "content: 'Launch moves to Monday.'" in write
    assert "status: 'CAPTURED'" in write
    assert "ingestion_kind: 'EXTRACTED'" in write


def test_repeated_capture_does_not_write_duplicate_event():
    db = CaptureDB(exists=True)

    result = SourceEventStore(db).capture(event())

    assert result["created"] is False
    assert len(db.queries) == 1


def test_oracle_capture_is_immediately_processed():
    db = CaptureDB()
    SourceEventStore(db).capture(event(), ingestion_kind="ORACLE")
    assert "status: 'PROCESSED'" in db.queries[-1]


def test_start_extraction_records_numbered_attempt():
    db = AttemptDB(attempt_count=2)

    result = SourceEventStore(db).start_extraction(
        "source-event:slack:message-42", "openrouter", "deepseek", "v1"
    )

    assert result["extraction_key"].endswith(":extraction:3")
    assert result["status"] == "RUNNING"
    assert "READ_FROM" in db.queries[-1]


def test_start_extraction_rejects_processed_event_without_reprocess():
    db = AttemptDB(event_status="PROCESSED")

    with pytest.raises(ValueError, match="already processed"):
        SourceEventStore(db).start_extraction("event", "provider", "model", "v1")

    assert not any("CREATE" in query for query in db.queries)


def test_complete_extraction_links_claims_and_updates_states():
    db = AttemptDB()

    result = SourceEventStore(db).complete_extraction("extraction", ["claim-1"])

    assert result == {"extraction_key": "extraction", "status": "SUCCEEDED"}
    assert any("PRODUCED_BY" in query for query in db.queries)
    assert any("event.status = 'PROCESSED'" in query for query in db.queries)


def test_failure_records_step_error_and_traceback_and_stops_state():
    db = AttemptDB()

    try:
        raise RuntimeError("model stopped")
    except RuntimeError as exc:
        result = SourceEventStore(db).fail_extraction("extraction", "EXTRACT", exc)

    assert result["status"] == "FAILED"
    write = "\n".join(db.queries)
    assert "FailureRecord" in write
    assert "error_type: 'RuntimeError'" in write
    assert "model stopped" in write
    assert "Traceback" in write
    assert "event.status = 'FAILED'" in write
