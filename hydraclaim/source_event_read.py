"""Bounded audit reads for durable source events."""

from __future__ import annotations

import json
from typing import Any

from hydraclaim.model import graph_id
from hydraclaim.source_events import _content_hash
from hydraclaim.subject_context import (
    subject_context_digest,
    validate_subject_context_source,
)


def _limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("limit must be an integer from 1 through 100")
    return value


def list_events(db: Any, *, limit: int = 20) -> list[dict]:
    """Return bounded source-event summaries in stable order."""
    limit = _limit(limit)
    return db.query(
        "MATCH (event:SourceEvent) "
        "RETURN event.key AS key, event.source_kind AS source_kind, "
        "event.author AS author, event.occurred_at AS occurred_at, "
        "event.captured_at AS captured_at, event.status AS status "
        f"ORDER BY event.captured_at DESC, event.id DESC LIMIT {limit}"
    )


def _public_subject_context(context: dict) -> dict:
    public = {**context}
    public["subjects"] = [
        {key: value for key, value in subject.items() if key != "actor_ref"}
        for subject in context["subjects"]
    ]
    public["messages"] = [
        {
            key: value
            for key, value in message.items()
            if key not in {"actor_ref", "source_actor_ref"}
        }
        for message in context["messages"]
    ]
    return public


def read_event(
    db: Any, event_key: str, *, include_private_context: bool = False
) -> dict:
    """Return one event and its complete bounded provenance."""
    if not isinstance(event_key, str) or not event_key.strip():
        raise ValueError("event_key must be a non-empty string")
    event_id = graph_id(event_key)
    scope = f"(event:SourceEvent {{id: {event_id}}})"
    events = db.query(
        f"MATCH {scope} RETURN event.key AS key, event.source_kind AS source_kind, "
        "event.author AS author, event.occurred_at AS occurred_at, "
        "event.captured_at AS captured_at, event.content AS content, "
        "event.status AS status, event.ingestion_kind AS ingestion_kind, "
        "event.subject_context_json AS subject_context_json, "
        "event.subject_context_digest AS subject_context_digest, "
        "event.content_hash AS content_hash"
    )
    if not events:
        raise ValueError(f"source event not found: {event_key!r}")
    private_context_column = (
        ", extraction.subject_context_json AS subject_context_json"
        if include_private_context
        else ""
    )
    extractions = db.query(
        f"MATCH (extraction:Extraction)-[:READ_FROM]->{scope} "
        "RETURN extraction.key AS key, extraction.provider AS provider, "
        "extraction.model AS model, extraction.prompt_version AS prompt_version, "
        "extraction.started_at AS started_at, extraction.finished_at AS finished_at, "
        "extraction.status AS status, "
        "extraction.subject_context_digest AS subject_context_digest, "
        "extraction.subject_context_provenance AS subject_context_provenance"
        + private_context_column
        + " "
        "ORDER BY extraction.started_at, extraction.id LIMIT 100"
    )
    failures = db.query(
        f"MATCH (extraction:Extraction)-[:READ_FROM]->{scope} "
        "MATCH (extraction)-[:FAILED_WITH]->(failure:FailureRecord) "
        "RETURN failure.step AS step, failure.error_type AS error_type, "
        "failure.message AS message, failure.traceback AS traceback, "
        "failure.failed_at AS failed_at ORDER BY failure.failed_at, failure.id LIMIT 100"
    )
    claims = db.query(
        f"MATCH (claim:Claim)-[:PRODUCED_BY]->(extraction:Extraction)-[:READ_FROM]->{scope} "
        "RETURN claim.key AS key ORDER BY claim.key LIMIT 100"
    )
    if include_private_context:
        for extraction in extractions:
            context_json = extraction.pop("subject_context_json", None)
            if context_json:
                try:
                    extraction["subject_context"] = json.loads(context_json)
                except (TypeError, json.JSONDecodeError):
                    extraction["subject_context"] = context_json
    event = dict(events[0])
    context_json = event.pop("subject_context_json", None)
    if include_private_context:
        event["subject_context_present"] = (
            context_json is not None or event.get("subject_context_digest") is not None
        )
    elif context_json is None and event.get("subject_context_digest") is not None:
        raise ValueError(
            "stored subject context digest exists but context JSON is missing"
        )
    if context_json is not None:
        try:
            decoded = json.loads(context_json)
        except (TypeError, json.JSONDecodeError) as exc:
            if not include_private_context:
                raise ValueError("stored subject context is invalid") from exc
            event["subject_context"] = context_json
        else:
            if include_private_context:
                event["subject_context"] = decoded
            else:
                try:
                    context = validate_subject_context_source(decoded, event)
                except ValueError as exc:
                    raise ValueError("stored subject context is invalid") from exc
                if _content_hash(
                    {
                        **event,
                        "subject_context": context,
                    }
                ) != event.get("content_hash"):
                    raise ValueError(
                        "stored source event content or subject context failed integrity check"
                    )
                if subject_context_digest(context) != event.get(
                    "subject_context_digest"
                ):
                    raise ValueError(
                        "stored subject context digest failed integrity check"
                    )
                event["subject_context"] = _public_subject_context(context)
    return {
        "event": event,
        "extractions": extractions,
        "failures": failures,
        "claims": claims,
    }


def event_status(db: Any) -> dict:
    """Return event state counts and every visible failed event."""
    rows = db.query(
        "MATCH (event:SourceEvent) RETURN event.status AS status, count(*) AS count"
    )
    counts = {"CAPTURED": 0, "PROCESSED": 0, "FAILED": 0}
    for row in rows:
        if row.get("status") in counts:
            counts[row["status"]] = int(row["count"])
    failures = db.query(
        "MATCH (extraction:Extraction)-[:READ_FROM]->(event:SourceEvent {status: 'FAILED'}) "
        "MATCH (extraction)-[:FAILED_WITH]->(failure:FailureRecord) "
        "RETURN event.key AS event_key, failure.step AS step "
        "ORDER BY failure.failed_at DESC, failure.id DESC LIMIT 100"
    )
    return {"counts": counts, "failures": failures}
