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
