import pytest

from hydraclaim.source_event_read import event_status, list_events, read_event


class ReadDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def query(self, cypher, consistency="causal"):
        self.queries.append(cypher)
        return self.responses.pop(0)


def test_list_events_is_bounded_and_newest_first():
    db = ReadDB([[{"key": "event-2", "status": "CAPTURED"}]])
    assert list_events(db, limit=5)[0]["key"] == "event-2"
    assert "ORDER BY event.captured_at DESC" in db.queries[0]
    assert "LIMIT 5" in db.queries[0]


def test_list_events_rejects_invalid_limit_before_query():
    db = ReadDB([])
    with pytest.raises(ValueError, match="limit"):
        list_events(db, limit=0)
    assert db.queries == []


def test_read_event_selects_identifier_before_related_nodes():
    db = ReadDB(
        [
            [{"key": "event", "content": "exact", "status": "FAILED"}],
            [{"key": "event:extraction:1", "status": "FAILED"}],
            [{"step": "EXTRACT", "message": "stopped"}],
            [{"key": "claim-1"}],
        ]
    )
    result = read_event(db, "event")
    assert result["event"]["content"] == "exact"
    assert result["failures"][0]["step"] == "EXTRACT"
    assert all("event:SourceEvent {id:" in query for query in db.queries)


def test_read_event_fails_for_unknown_identifier():
    with pytest.raises(ValueError, match="not found"):
        read_event(ReadDB([[]]), "missing")


def test_event_status_returns_counts_and_failed_event_details():
    db = ReadDB(
        [
            [{"status": "CAPTURED", "count": 2}, {"status": "FAILED", "count": 1}],
            [{"event_key": "event", "step": "WRITE"}],
        ]
    )
    assert event_status(db) == {
        "counts": {"CAPTURED": 2, "PROCESSED": 0, "FAILED": 1},
        "failures": [{"event_key": "event", "step": "WRITE"}],
    }
