"""Durable source-event writes for HydraClaim."""

from __future__ import annotations

import hashlib
import traceback
from datetime import datetime, timezone
from typing import Any

from hydraclaim.claims import SOURCE_KINDS
from hydraclaim.cypher import to_cypher_literal as lit
from hydraclaim.model import graph_id, source_event_props


_FIELDS = frozenset({"source_kind", "author", "occurred_at", "content", "source_id"})


def validate_source_event(value: object) -> dict:
    """Return one validated source event before any graph operation."""
    if not isinstance(value, dict):
        raise ValueError("source event must be an object")
    errors: list[str] = []
    unsupported = sorted(set(value) - _FIELDS)
    if unsupported:
        errors.append(f"unsupported fields: {unsupported}")
    source_kind = value.get("source_kind")
    if source_kind not in SOURCE_KINDS:
        errors.append(f"source_kind is not supported: {source_kind!r}")
    for field in ("author", "occurred_at", "content"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field} must be a non-empty string")
    source_id = value.get("source_id")
    if source_id is not None and (
        not isinstance(source_id, str) or not source_id.strip()
    ):
        errors.append("source_id must be a non-empty string")
    occurred_at = value.get("occurred_at")
    if isinstance(occurred_at, str) and occurred_at.strip():
        try:
            datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("occurred_at must be a valid ISO timestamp")
    if errors:
        raise ValueError("invalid source event: " + "; ".join(errors))
    return dict(value)


def _content_hash(event: dict) -> str:
    text = "\x1f".join(
        (event["source_kind"], event["author"], event["occurred_at"], event["content"])
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_event_key(event: dict) -> str:
    """Return the stable external key for one validated source event."""
    checked = validate_source_event(event)
    identity = checked.get("source_id") or _content_hash(checked)
    return f"source-event:{checked['source_kind']}:{identity}"


def _props(properties: dict) -> str:
    return (
        "{"
        + ", ".join(f"{name}: {lit(value)}" for name, value in properties.items())
        + "}"
    )


class SourceEventStore:
    """Write accepted source events through the HydraDB query seam."""

    def __init__(self, db: Any):
        self._db = db

    def capture(self, event: dict, *, ingestion_kind: str = "EXTRACTED") -> dict:
        checked = validate_source_event(event)
        if ingestion_kind not in {"EXTRACTED", "ORACLE"}:
            raise ValueError(f"ingestion_kind is not supported: {ingestion_kind!r}")
        key = source_event_key(checked)
        node_id = graph_id(key)
        existing = self._db.query_one(
            f"MATCH (event:SourceEvent {{id: {node_id}}}) RETURN count(*) AS c"
        )
        if existing and existing.get("c", 0):
            return {"event_key": key, "status": "CAPTURED", "created": False}
        captured_at = datetime.now(timezone.utc).isoformat()
        properties = source_event_props(
            checked, key, captured_at, _content_hash(checked)
        )
        properties["ingestion_kind"] = ingestion_kind
        if ingestion_kind == "ORACLE":
            properties["status"] = "PROCESSED"
        self._db.query(f"CREATE (event:SourceEvent {_props(properties)})")
        return {"event_key": key, "status": properties["status"], "created": True}

    def start_extraction(
        self,
        event_key: str,
        provider: str,
        model: str,
        prompt_version: str,
        *,
        reprocess: bool = False,
    ) -> dict:
        """Create one explicit processing attempt for an accepted event."""
        for name, value in (
            ("event_key", event_key),
            ("provider", provider),
            ("model", model),
            ("prompt_version", prompt_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        event_id = graph_id(event_key)
        event = self._db.query_one(
            f"MATCH (event:SourceEvent {{id: {event_id}}}) "
            "RETURN event.status AS status"
        )
        if not event:
            raise ValueError(f"source event not found: {event_key!r}")
        if event["status"] == "PROCESSED" and not reprocess:
            raise ValueError(f"source event is already processed: {event_key!r}")
        count = self._db.query_one(
            f"MATCH (extraction:Extraction)-[:READ_FROM]->"
            f"(event:SourceEvent {{id: {event_id}}}) RETURN count(*) AS c"
        )
        attempt = int(count.get("c", 0)) + 1
        extraction_key = f"{event_key}:extraction:{attempt}"
        extraction_id = graph_id(extraction_key)
        started_at = datetime.now(timezone.utc).isoformat()
        properties = {
            "id": extraction_id,
            "key": extraction_key,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "started_at": started_at,
            "finished_at": "",
            "status": "RUNNING",
        }
        self._db.query(
            f"CREATE (extraction:Extraction {_props(properties)})-[:READ_FROM]->"
            f"(event:SourceEvent {{id: {event_id}}})"
        )
        return {"extraction_key": extraction_key, "status": "RUNNING"}

    def _running_extraction(self, extraction_key: str) -> dict:
        extraction_id = graph_id(extraction_key)
        row = self._db.query_one(
            f"MATCH (extraction:Extraction {{id: {extraction_id}}})-[:READ_FROM]->"
            "(event:SourceEvent) RETURN extraction.status AS status, "
            "event.id AS event_id, event.key AS event_key"
        )
        if not row:
            raise ValueError(f"extraction not found: {extraction_key!r}")
        if row["status"] != "RUNNING":
            raise ValueError(f"extraction is not running: {extraction_key!r}")
        return row

    def complete_extraction(self, extraction_key: str, claim_keys: list[str]) -> dict:
        """Link produced claims and mark one attempt and event complete."""
        row = self._running_extraction(extraction_key)
        extraction_id = graph_id(extraction_key)
        for claim_key in claim_keys:
            self._db.query(
                f"CREATE (claim:Claim {{id: {graph_id(claim_key)}}})-[:PRODUCED_BY]->"
                f"(extraction:Extraction {{id: {extraction_id}}})"
            )
        finished_at = datetime.now(timezone.utc).isoformat()
        self._db.query(
            f"MATCH (extraction:Extraction {{id: {extraction_id}}}) "
            f"SET extraction.status = 'SUCCEEDED', extraction.finished_at = {lit(finished_at)}"
        )
        self._db.query(
            f"MATCH (event:SourceEvent {{id: {int(row['event_id'])}}}) "
            "SET event.status = 'PROCESSED'"
        )
        return {"extraction_key": extraction_key, "status": "SUCCEEDED"}

    def fail_extraction(self, extraction_key: str, step: str, exc: Exception) -> dict:
        """Record one stopped step with full error context."""
        if step not in {"EXTRACT", "RECONCILE", "WRITE"}:
            raise ValueError(f"failure step is not supported: {step!r}")
        row = self._running_extraction(extraction_key)
        extraction_id = graph_id(extraction_key)
        failed_at = datetime.now(timezone.utc).isoformat()
        failure_key = f"{extraction_key}:failure"
        failure = {
            "id": graph_id(failure_key),
            "key": failure_key,
            "step": step,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(exc)),
            "failed_at": failed_at,
        }
        self._db.query(
            f"CREATE (extraction:Extraction {{id: {extraction_id}}})-[:FAILED_WITH]->"
            f"(failure:FailureRecord {_props(failure)})"
        )
        self._db.query(
            f"MATCH (extraction:Extraction {{id: {extraction_id}}}) "
            f"SET extraction.status = 'FAILED', extraction.finished_at = {lit(failed_at)}"
        )
        self._db.query(
            f"MATCH (event:SourceEvent {{id: {int(row['event_id'])}}}) "
            "SET event.status = 'FAILED'"
        )
        return {"extraction_key": extraction_key, "status": "FAILED", "step": step}
