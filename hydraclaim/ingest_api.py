"""Write-path API handlers for ad-hoc ingestion.

POST /ingest accepts either:
  {"text", "source_kind", "author", "channel"}  -- raw text, needs LLM
  {"sessions": [...], "entities": [...]}         -- pre-formatted scenario

POST /ingest/slack accepts Slack export JSON (channel messages array).

Both paths run: entity discovery -> LLM extraction -> reconcile -> HydraDB write.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from hydraclaim.claims import SOURCE_KINDS
from hydraclaim.db import HydraDB
from hydraclaim.extract import extract_session
from hydraclaim.pipeline import fetch_active_claims, run_pipeline
from hydraclaim.reconcile import apply_plan, plan_writes
from hydraclaim.slack_import import parse_slack_export


logger = logging.getLogger(__name__)


def _require_llm() -> None:
    if not os.environ.get("LLM_API_KEY"):
        raise IngestionError(
            "LLM_API_KEY is not configured — ingestion requires an LLM"
        )


class IngestionError(RuntimeError):
    pass


def _request_summary(body: Any) -> dict[str, Any]:
    """Return request metadata without source text or document contents."""
    if not isinstance(body, dict):
        return {"type": type(body).__name__}

    summary: dict[str, Any] = {"fields": sorted(body)}
    text = body.get("text")
    if isinstance(text, str):
        summary["text_length"] = len(text)
    if isinstance(body.get("sessions"), list):
        summary["session_count"] = len(body["sessions"])
    if isinstance(body.get("entities"), list):
        summary["entity_count"] = len(body["entities"])
    if isinstance(body.get("messages"), list):
        summary["message_count"] = len(body["messages"])
    return summary


def _log_failure(step: str, scenario_id: str, request_summary: dict[str, Any]) -> None:
    logger.exception(
        "ingest failed step=%s scenario=%s input=%r",
        step,
        scenario_id,
        request_summary,
    )


def _discover_entities_llm(text: str) -> list[dict]:
    """Ask the LLM to extract entities from raw text."""
    from hydraclaim.llm import chat_json

    messages = [
        {
            "role": "system",
            "content": (
                "Extract named entities from the text. Return strict JSON:\n"
                '{"entities": [{"name": "...", "type": "person|project|system|team|unknown", '
                '"aliases": ["..."]}]}\n'
                "Only include proper nouns (people, projects, systems, teams). "
                "Deduplicate. Keep it short."
            ),
        },
        {"role": "user", "content": text},
    ]
    result = chat_json(messages)
    entities = result.get("entities", []) if isinstance(result, dict) else []
    for e in entities:
        e.setdefault("type", "unknown")
        e.setdefault("aliases", [])
    return entities


def _wrap_raw_text(text: str, source_kind: str, author: str, channel: str) -> dict:
    """Wrap raw text into a single-message session document."""
    now = datetime.now(timezone.utc)
    session_id = f"adhoc-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    return {
        "session_id": session_id,
        "messages": [
            {
                "msg_id": f"{session_id}-m001",
                "ts": now.isoformat(),
                "author": author,
                "source_kind": source_kind,
                "channel": channel,
                "text": text,
            }
        ],
    }


def handle_ingest(body: dict, db: HydraDB) -> tuple[int, dict]:
    """Handle POST /ingest."""
    try:
        _require_llm()
    except IngestionError as e:
        return 503, {"code": "ingest_unavailable", "error": str(e)}

    step = "validate"
    scenario_id = "unknown"
    request_summary = _request_summary(body)
    try:
        if "sessions" in body:
            scenario_id = body.get("scenario_id", "upload-pending")
            step = "pipeline"
            return _ingest_preformatted(body, db)

        text = (body.get("text") or "").strip()
        if not text:
            return 400, {
                "code": "invalid_request",
                "error": "missing 'text' in request body",
            }

        source_kind = body.get("source_kind", "slack")
        if source_kind not in SOURCE_KINDS:
            return 400, {
                "code": "invalid_request",
                "error": f"invalid source_kind: {source_kind!r}",
            }

        author = body.get("author", "unknown")
        channel = body.get("channel", "adhoc")
        scenario_id = f"adhoc-{uuid.uuid4().hex[:8]}"

        step = "discover"
        entities = _discover_entities_llm(text)
        session = _wrap_raw_text(text, source_kind, author, channel)
        step = "fetch_active"
        active = fetch_active_claims(db)

        step = "extract"
        drafts, warnings = extract_session(session, entities, active)
        for i, d in enumerate(drafts):
            d["id"] = f"{scenario_id}:x{i + 1}"

        step = "plan"
        plan = plan_writes(drafts, active, entities, id_prefix=scenario_id)
        warnings.extend(plan["warnings"])
        step = "apply"
        applied = apply_plan(db, plan, scenario_id, entities)

        return 200, {
            "scenario_id": scenario_id,
            "created": applied["created"],
            "superseded": applied["superseded"],
            "contradicted": applied["contradicted"],
            "duplicates": applied["duplicates"],
            "warnings": warnings,
        }
    except Exception:
        _log_failure(step, scenario_id, request_summary)
        return 500, {"code": "ingest_failed", "error": "ingestion failed"}


def _ingest_preformatted(body: dict, db: HydraDB) -> tuple[int, dict]:
    """Ingest a pre-formatted scenario document."""
    sessions = body.get("sessions", [])
    entities = body.get("entities", [])
    scenario_id = body.get("scenario_id", f"upload-{uuid.uuid4().hex[:8]}")

    doc = {
        "scenario_id": scenario_id,
        "sessions": sessions,
        "entities": entities,
    }
    stats = run_pipeline(db, doc)
    return 200, stats


def handle_ingest_slack(body: Any, db: HydraDB) -> tuple[int, dict]:
    """Handle POST /ingest/slack."""
    try:
        _require_llm()
    except IngestionError as e:
        return 503, {"code": "ingest_unavailable", "error": str(e)}

    step = "validate"
    scenario_id = "unknown"
    request_summary = _request_summary(body)
    try:
        channel = (
            body.get("channel", "general") if isinstance(body, dict) else "general"
        )
        messages = body.get("messages", body) if isinstance(body, dict) else body
        if not isinstance(messages, list):
            return 400, {
                "code": "invalid_request",
                "error": "expected a JSON array of Slack messages",
            }

        step = "parse_slack"
        sessions = parse_slack_export(messages, channel)
        if not sessions:
            return 400, {
                "code": "invalid_request",
                "error": "no messages found in Slack export",
            }

        scenario_id = f"slack-{channel}-{uuid.uuid4().hex[:6]}"
        step = "discover"
        entities = _discover_entities_from_sessions(sessions)
        all_warnings: list[str] = []
        total = {"created": 0, "superseded": 0, "contradicted": 0, "duplicates": 0}

        for session in sessions:
            step = "fetch_active"
            active = fetch_active_claims(db)
            step = "extract"
            drafts, warnings = extract_session(session, entities, active)
            all_warnings.extend(warnings)
            for i, d in enumerate(drafts):
                d["id"] = f"{scenario_id}:{session['session_id']}:x{i + 1}"
            step = "plan"
            plan = plan_writes(
                drafts,
                active,
                entities,
                id_prefix=f"{scenario_id}:{session['session_id']}",
            )
            all_warnings.extend(plan["warnings"])
            step = "apply"
            applied = apply_plan(db, plan, scenario_id, entities)
            total["created"] += applied["created"]
            total["superseded"] += applied["superseded"]
            total["contradicted"] += applied["contradicted"]
            total["duplicates"] += applied["duplicates"]

        return 200, {
            "scenario_id": scenario_id,
            "sessions_processed": len(sessions),
            **total,
            "warnings": all_warnings,
        }
    except Exception:
        _log_failure(step, scenario_id, request_summary)
        return 500, {"code": "ingest_failed", "error": "ingestion failed"}


def _discover_entities_from_sessions(sessions: list[dict]) -> list[dict]:
    """Extract entities from all session messages combined."""
    all_text = "\n".join(m["text"] for s in sessions for m in s.get("messages", []))
    if len(all_text) > 8000:
        all_text = all_text[:8000]
    return _discover_entities_llm(all_text)
