from __future__ import annotations

import pytest

from hydraclaim.source_events import (
    SourceEventStore,
    _content_hash,
    source_event_key,
)


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


def subject_context(actor_ref="whatsapp:owner"):
    return {
        "version": 1,
        "producer": "jarvis-recorder",
        "subjects": [
            {
                "actor_ref": actor_ref,
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
                "text": "Launch moves to Monday.",
                "source_actor_ref": "discord:42",
                "actor_ref": actor_ref,
                "role": "user",
            }
        ],
    }


class ContextCaptureDB(CaptureDB):
    def __init__(self, existing_hash=None):
        super().__init__(exists=existing_hash is not None)
        self.existing_hash = existing_hash

    def query_one(self, cypher: str):
        self.queries.append(cypher)
        if "RETURN count(*) AS c" in cypher:
            return {"c": int(self.exists)}
        if "RETURN event.content_hash AS content_hash" in cypher:
            return {"content_hash": self.existing_hash}
        return {"c": 0}


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


def test_subject_context_changes_content_hash_but_preserves_source_key():
    first = context_event(subject_context())
    same = context_event(subject_context())
    changed = context_event(subject_context("whatsapp:other"))

    assert source_event_key(first) == source_event_key(changed)
    assert _content_hash(first) == _content_hash(same)
    assert _content_hash(first) != _content_hash(changed)

    first.pop("source_id")
    changed.pop("source_id")
    assert source_event_key(first) == source_event_key(changed)


def test_capture_persists_canonical_private_context_and_digest():
    source = context_event(subject_context())
    db = ContextCaptureDB()

    SourceEventStore(db).capture(source)

    write = db.queries[-1]
    assert "subject_context_json:" in write
    assert '"actor_ref":"whatsapp:owner"' in write
    assert "subject_context_digest:" in write
    assert "content_hash:" in write


def test_capture_rejects_reused_source_key_with_changed_context():
    original = context_event(subject_context())
    db = ContextCaptureDB(existing_hash=_content_hash(original))

    with pytest.raises(ValueError, match="conflicting content or subject context"):
        SourceEventStore(db).capture(context_event(subject_context("whatsapp:other")))

    assert len(db.queries) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value.update(content="different"), "reconstruct source content"),
        (
            lambda value: value.update(occurred_at="2026-08-20T10:31:00+00:00"),
            "latest message time",
        ),
    ],
)
def test_capture_rejects_context_that_does_not_match_source_before_query(
    change, message
):
    value = context_event(subject_context())
    change(value)
    db = ContextCaptureDB()

    with pytest.raises(ValueError, match=message):
        SourceEventStore(db).capture(value)

    assert db.queries == []


def context_event(context):
    return event(
        occurred_at=max(message["ts"] for message in context["messages"]),
        content="\n".join(
            f"{message['author']}: {message['text']}" for message in context["messages"]
        ),
        subject_context=context,
    )


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
    db = ContextCaptureDB(existing_hash=_content_hash(event()))

    result = SourceEventStore(db).capture(event())

    assert result["created"] is False
    assert len(db.queries) == 2


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


def test_start_extraction_records_context_digest_provenance_and_private_attestation():
    db = AttemptDB()

    SourceEventStore(db).start_extraction(
        "source-event:slack:message-42",
        "openrouter",
        "deepseek",
        "v1",
        subject_context_digest="digest-1",
        subject_context_provenance="operator_attestation",
        subject_context_json='{"version":1}',
    )

    write = db.queries[-1]
    assert "subject_context_digest: 'digest-1'" in write
    assert "subject_context_provenance: 'operator_attestation'" in write
    assert "subject_context_json: '{\"version\":1}'" in write


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
