import json

import pytest

from hydraclaim.source_event_read import event_status, list_events, read_event
from hydraclaim.source_events import _content_hash
from hydraclaim.subject_context import subject_context_digest


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


def test_read_event_hides_actor_references_unless_operator_requests_them():
    context = {
        "version": 1,
        "producer": "jarvis-recorder",
        "subjects": [
            {
                "actor_ref": "whatsapp:owner",
                "name": "owner",
                "aliases": ["owner"],
                "type": "unknown",
            }
        ],
        "messages": [
            {
                "msg_id": "q:42",
                "ts": "2026-08-20T10:30:00+00:00",
                "author": "Owner display",
                "text": "I prefer tea.",
                "source_actor_ref": "discord:42",
                "actor_ref": "whatsapp:owner",
                "role": "user",
            }
        ],
    }
    event = {
        "key": "event",
        "source_kind": "slack",
        "author": "Conversation",
        "occurred_at": "2026-08-20T10:30:00+00:00",
        "content": "Owner display: I prefer tea.",
        "subject_context_json": json.dumps(context),
        "subject_context_digest": subject_context_digest(context),
    }
    event["content_hash"] = _content_hash({**event, "subject_context": context})
    public = read_event(ReadDB([[event], [], [], []]), "event")
    private = read_event(
        ReadDB([[event], [], [], []]), "event", include_private_context=True
    )

    assert "actor_ref" not in public["event"]["subject_context"]["subjects"][0]
    assert "source_actor_ref" not in public["event"]["subject_context"]["messages"][0]
    assert public["event"]["subject_context"]["messages"][0]["role"] == "user"
    assert (
        private["event"]["subject_context"]["subjects"][0]["actor_ref"]
        == "whatsapp:owner"
    )


@pytest.mark.parametrize("stored_json", [None, "", "null", "false", "0"])
def test_private_read_preserves_context_presence_when_json_is_missing_or_empty(
    stored_json,
):
    event = {
        "key": "event",
        "source_kind": "slack",
        "author": "Conversation",
        "occurred_at": "2026-08-20T10:30:00+00:00",
        "content": "Owner display: I prefer tea.",
        "subject_context_json": stored_json,
        "subject_context_digest": "stored-digest",
    }
    private = read_event(
        ReadDB([[event], [], [], []]), "event", include_private_context=True
    )["event"]

    assert private["subject_context_present"] is True
    assert private["subject_context_digest"] == "stored-digest"
    if stored_json in {"null", "false", "0"}:
        assert private["subject_context"] in {None, False, 0}


def test_public_read_fails_when_context_digest_exists_without_json():
    event = {
        "key": "event",
        "source_kind": "slack",
        "author": "Conversation",
        "occurred_at": "2026-08-20T10:30:00+00:00",
        "content": "Owner display: I prefer tea.",
        "subject_context_json": None,
        "subject_context_digest": "stored-digest",
    }

    with pytest.raises(ValueError, match="context JSON is missing"):
        read_event(ReadDB([[event], [], [], []]), "event")


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
