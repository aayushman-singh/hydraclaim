"""Bounded audit reads for durable source events."""

from __future__ import annotations

from typing import Any

from hydraclaim.model import graph_id


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


def read_event(db: Any, event_key: str) -> dict:
    """Return one event and its complete bounded provenance."""
    if not isinstance(event_key, str) or not event_key.strip():
        raise ValueError("event_key must be a non-empty string")
    event_id = graph_id(event_key)
    scope = f"(event:SourceEvent {{id: {event_id}}})"
    events = db.query(
        f"MATCH {scope} RETURN event.key AS key, event.source_kind AS source_kind, "
        "event.author AS author, event.occurred_at AS occurred_at, "
        "event.captured_at AS captured_at, event.content AS content, "
        "event.status AS status, event.ingestion_kind AS ingestion_kind"
    )
    if not events:
        raise ValueError(f"source event not found: {event_key!r}")
    extractions = db.query(
        f"MATCH (extraction:Extraction)-[:READ_FROM]->{scope} "
        "RETURN extraction.key AS key, extraction.provider AS provider, "
        "extraction.model AS model, extraction.prompt_version AS prompt_version, "
        "extraction.started_at AS started_at, extraction.finished_at AS finished_at, "
        "extraction.status AS status ORDER BY extraction.started_at, extraction.id LIMIT 100"
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
    return {
        "event": events[0],
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
