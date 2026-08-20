"""Durable source-event writes for HydraClaim."""

from __future__ import annotations

import hashlib
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
        self._db.query(f"CREATE (event:SourceEvent {_props(properties)})")
        return {"event_key": key, "status": "CAPTURED", "created": True}
