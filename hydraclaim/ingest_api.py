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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hydraclaim.claims import SOURCE_KINDS
from hydraclaim.db import HydraDB, HydraDBError
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.extract import extract_session
from hydraclaim.llm import LLMError
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
    if isinstance(body, list):
        return {
            "input_id": "slack-list",
            "type": "list",
            "message_count": len(body),
        }
    if not isinstance(body, dict):
        return {"input_id": "unknown-input", "type": type(body).__name__}

    input_id = "slack-dict" if "messages" in body else "ingest-request"
    summary: dict[str, Any] = {"input_id": input_id, "fields": sorted(body)}
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


@dataclass
class _FailureContext:
    request_summary: dict[str, Any]
    step: str = "validate"
    scenario_id: str = "unknown"
    state: dict[str, Any] = field(default_factory=dict)

    def mark(self, step: str, **state: Any) -> None:
        self.step = step
        self.state = {"phase": step, **state}


def _key_value_summary(values: dict[str, Any]) -> str:
    return " ".join(f"{key}={value}" for key, value in values.items())


def _log_failure(context: _FailureContext, exc: Exception) -> None:
    logger.exception(
        "ingest failed step=%s scenario=%s state=%s exception_type=%s input=%s",
        context.step,
        context.scenario_id,
        _key_value_summary(context.state),
        type(exc).__name__,
        _key_value_summary(context.request_summary),
    )


def _typed_failure(context: _FailureContext, exc: Exception) -> tuple[int, dict]:
    _log_failure(context, exc)
    if isinstance(exc, GraphIntegrityError):
        return 409, {"code": "graph_integrity_error", "error": "graph integrity error"}
    if isinstance(exc, HydraDBError):
        return 502, {"code": "graph_backend_failed", "error": "graph backend failed"}
    if isinstance(exc, LLMError):
        return 502, {"code": "llm_failed", "error": "language model request failed"}
    if isinstance(exc, ValueError):
        return 400, {"code": "invalid_request", "error": "invalid ingestion input"}
    return 500, {"code": "ingest_failed", "error": "ingestion failed"}


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
    if not isinstance(result, dict) or set(result) != {"entities"}:
        raise ValueError("entity extraction response must be an object with entities")
    entities = result["entities"]
    if not isinstance(entities, list):
        raise ValueError("entity extraction response entities must be a list")
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or set(entity) != {"name", "type", "aliases"}:
            raise ValueError(f"entity extraction item {index} has invalid fields")
        if not isinstance(entity["name"], str) or not entity["name"].strip():
            raise ValueError(f"entity extraction item {index} has invalid name")
        if not isinstance(entity["type"], str) or not entity["type"].strip():
            raise ValueError(f"entity extraction item {index} has invalid type")
        if not isinstance(entity["aliases"], list) or any(
            not isinstance(alias, str) for alias in entity["aliases"]
        ):
            raise ValueError(f"entity extraction item {index} has invalid aliases")
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

    context = _FailureContext(_request_summary(body))
    try:
        if "sessions" in body:
            context.scenario_id = body.get("scenario_id", "upload-pending")
            context.mark(
                "validate",
                session_count=len(body.get("sessions", [])),
                entity_count=len(body.get("entities", [])),
            )
            return _ingest_preformatted(body, db, context)

        text = (body.get("text") or "").strip()
        if not text:
            return 400, {
                "code": "invalid_request",
                "error": "missing 'text' in request body",
            }

        source_kind = body.get("source_kind")
        if source_kind not in SOURCE_KINDS:
            return 400, {
                "code": "invalid_request",
                "error": f"invalid source_kind: {source_kind!r}",
            }

        author = body.get("author")
        if not isinstance(author, str) or not author.strip():
            return 400, {
                "code": "invalid_request",
                "error": "missing 'author' in request body",
            }
        channel = body.get("channel")
        if not isinstance(channel, str) or not channel.strip():
            return 400, {
                "code": "invalid_request",
                "error": "missing 'channel' in request body",
            }
        context.scenario_id = f"adhoc-{uuid.uuid4().hex[:8]}"

        context.mark("discover_entities", text_length=len(text))
        entities = _discover_entities_llm(text)
        session = _wrap_raw_text(text, source_kind, author, channel)
        context.mark("read_active")
        active = fetch_active_claims(db)
        context.mark("read_active", active_count=len(active))

        context.mark("extract", active_count=len(active))
        drafts, warnings = extract_session(session, entities, active)
        context.mark("extract", active_count=len(active), draft_count=len(drafts))
        for i, d in enumerate(drafts):
            d["id"] = f"{context.scenario_id}:x{i + 1}"

        context.mark("reconcile", active_count=len(active), draft_count=len(drafts))
        plan = plan_writes(drafts, active, entities, id_prefix=context.scenario_id)
        warnings.extend(plan["warnings"])
        context.mark(
            "graph_write",
            draft_count=len(drafts),
            plan_create_count=len(plan["create"]),
            plan_supersede_count=len(plan["supersede"]),
            plan_contradict_count=len(plan["contradict"]),
            plan_duplicate_count=plan["duplicates"],
            applied=False,
        )
        applied = apply_plan(db, plan, context.scenario_id, entities)
        context.state["applied"] = True

        return 200, {
            "scenario_id": context.scenario_id,
            "created": applied["created"],
            "superseded": applied["superseded"],
            "contradicted": applied["contradicted"],
            "duplicates": applied["duplicates"],
            "warnings": warnings,
        }
    except Exception as exc:
        return _typed_failure(context, exc)


def _ingest_preformatted(
    body: dict, db: HydraDB, context: _FailureContext
) -> tuple[int, dict]:
    """Ingest a pre-formatted scenario document."""
    sessions = body.get("sessions", [])
    entities = body.get("entities", [])
    scenario_id = body.get("scenario_id", f"upload-{uuid.uuid4().hex[:8]}")
    context.mark(
        "validation",
        session_count=len(sessions) if isinstance(sessions, list) else 0,
        entity_count=len(entities) if isinstance(entities, list) else 0,
    )
    if not isinstance(sessions, list):
        raise ValueError("sessions must be a list")
    if not isinstance(entities, list):
        raise ValueError("entities must be a list")

    doc = {
        "scenario_id": scenario_id,
        "sessions": sessions,
        "entities": entities,
    }
    stats = run_pipeline(db, doc, step_hook=context.mark)
    return 200, stats


def handle_ingest_slack(body: Any, db: HydraDB) -> tuple[int, dict]:
    """Handle POST /ingest/slack."""
    try:
        _require_llm()
    except IngestionError as e:
        return 503, {"code": "ingest_unavailable", "error": str(e)}

    context = _FailureContext(_request_summary(body))
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

        context.mark("validate", message_count=len(messages))
        context.mark("parse_slack", message_count=len(messages))
        sessions = parse_slack_export(messages, channel)
        if not sessions:
            return 400, {
                "code": "invalid_request",
                "error": "no messages found in Slack export",
            }

        context.scenario_id = f"slack-{channel}-{uuid.uuid4().hex[:6]}"
        context.mark("discover_entities", session_count=len(sessions))
        entities = _discover_entities_from_sessions(sessions)
        all_warnings: list[str] = []
        total = {"created": 0, "superseded": 0, "contradicted": 0, "duplicates": 0}

        for session_index, session in enumerate(sessions):
            context.mark(
                "read_active",
                session_index=session_index,
                session_count=len(sessions),
            )
            active = fetch_active_claims(db)
            context.mark(
                "extract",
                session_index=session_index,
                session_count=len(sessions),
                active_count=len(active),
            )
            drafts, warnings = extract_session(session, entities, active)
            all_warnings.extend(warnings)
            for i, d in enumerate(drafts):
                d["id"] = f"{context.scenario_id}:{session['session_id']}:x{i + 1}"
            context.mark(
                "reconcile",
                session_index=session_index,
                session_count=len(sessions),
                active_count=len(active),
                draft_count=len(drafts),
            )
            plan = plan_writes(
                drafts,
                active,
                entities,
                id_prefix=f"{context.scenario_id}:{session['session_id']}",
            )
            all_warnings.extend(plan["warnings"])
            context.mark(
                "graph_write",
                session_index=session_index,
                session_count=len(sessions),
                draft_count=len(drafts),
                plan_create_count=len(plan["create"]),
                plan_supersede_count=len(plan["supersede"]),
                plan_contradict_count=len(plan["contradict"]),
                plan_duplicate_count=plan["duplicates"],
                applied=False,
            )
            applied = apply_plan(db, plan, context.scenario_id, entities)
            context.state["applied"] = True
            total["created"] += applied["created"]
            total["superseded"] += applied["superseded"]
            total["contradicted"] += applied["contradicted"]
            total["duplicates"] += applied["duplicates"]

        return 200, {
            "scenario_id": context.scenario_id,
            "sessions_processed": len(sessions),
            **total,
            "warnings": all_warnings,
        }
    except Exception as exc:
        return _typed_failure(context, exc)


def _discover_entities_from_sessions(sessions: list[dict]) -> list[dict]:
    """Extract entities from all session messages combined."""
    all_text = "\n".join(m["text"] for s in sessions for m in s.get("messages", []))
    if len(all_text) > 8000:
        all_text = all_text[:8000]
    return _discover_entities_llm(all_text)
