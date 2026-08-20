"""End-to-end ingestion: scenario JSON -> LLM extraction -> reconcile -> HydraDB.

Per session: extract claims (with the current active claims as context),
plan the writes deterministically, apply them, then re-read active claims
from the graph so the next session's prompt sees real graph ids.

CLI: python -m hydraclaim.pipeline SCENARIO_JSON [...]
Requires a live HydraDB node (scripts/dev-up.sh) and LLM_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from hydraclaim.db import HydraDB
from hydraclaim.errors import PipelineInputError
from hydraclaim.extract import _parse_timestamp, extract_session
from hydraclaim.graph_write import GraphWriter
from hydraclaim.reconcile import plan_writes
from hydraclaim.claims import SOURCE_KINDS
from hydraclaim.source_events import SourceEventStore


def _required_string(value: object, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")


def _timestamp(value: object, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    try:
        _parse_timestamp(value, label=path)
    except ValueError:
        errors.append(f"{path} must be a valid ISO timestamp")


def validate_pipeline_document(document: object) -> dict:
    """Validate the session document before any pipeline field access."""
    if not isinstance(document, dict):
        raise PipelineInputError("invalid pipeline document: root must be an object")

    errors: list[str] = []
    _required_string(document.get("scenario_id"), "scenario_id", errors)

    entities = document.get("entities")
    if not isinstance(entities, list):
        errors.append("entities must be a list")
    else:
        for index, entity in enumerate(entities):
            path = f"entities[{index}]"
            if not isinstance(entity, dict):
                errors.append(f"{path} must be an object")
                continue
            _required_string(entity.get("name"), f"{path}.name", errors)
            if "type" in entity and not isinstance(entity["type"], str):
                errors.append(f"{path}.type must be a string")
            aliases = entity.get("aliases", [])
            if not isinstance(aliases, list) or any(
                not isinstance(alias, str) for alias in aliases
            ):
                errors.append(f"{path}.aliases must be a list of strings")

    sessions = document.get("sessions")
    if not isinstance(sessions, list):
        errors.append("sessions must be a list")
    else:
        message_fields = (
            "msg_id",
            "ts",
            "author",
            "source_kind",
            "channel",
            "text",
        )
        for session_index, session in enumerate(sessions):
            session_path = f"sessions[{session_index}]"
            if not isinstance(session, dict):
                errors.append(f"{session_path} must be an object")
                continue
            _required_string(
                session.get("session_id"), f"{session_path}.session_id", errors
            )
            if "started_at" in session:
                _timestamp(
                    session.get("started_at"), f"{session_path}.started_at", errors
                )
            messages = session.get("messages")
            if not isinstance(messages, list):
                errors.append(f"{session_path}.messages must be a list")
                continue
            for message_index, message in enumerate(messages):
                message_path = f"{session_path}.messages[{message_index}]"
                if not isinstance(message, dict):
                    errors.append(f"{message_path} must be an object")
                    continue
                for field in message_fields:
                    _required_string(
                        message.get(field), f"{message_path}.{field}", errors
                    )
                _timestamp(message.get("ts"), f"{message_path}.ts", errors)
                if (
                    "source_kind" in message
                    and message.get("source_kind") not in SOURCE_KINDS
                ):
                    errors.append(
                        f"{message_path}.source_kind is not supported: "
                        f"{message.get('source_kind')!r}"
                    )

    if errors:
        raise PipelineInputError("invalid pipeline document: " + "; ".join(errors))
    return document


def fetch_active_claims(db: HydraDB) -> list[dict]:
    # Select the string `key` (not the integer graph id) so plan_writes mixes
    # cleanly with string draft ids; graph_id(key) reproduces the node id.
    rows = db.query("""
MATCH (c:Claim {status: 'active'})-[:ABOUT]->(e:Entity)
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(s:Source)
RETURN c.key AS id, e.name AS subject, c.predicate AS predicate,
       c.value AS value, c.valid_from AS valid_from,
       s.kind AS source_kind, s.author AS author""")
    for row in rows:
        if (
            not isinstance(row.get("source_kind"), str)
            or not row["source_kind"].strip()
        ):
            raise ValueError("active Claim has no valid source_kind")
        if not isinstance(row.get("author"), str) or not row["author"].strip():
            raise ValueError("active Claim has no valid author")
    return rows


def run_pipeline(
    db: HydraDB,
    doc: dict,
    step_hook: Callable[..., None] | None = None,
) -> dict:
    doc = validate_pipeline_document(doc)
    scen = doc["scenario_id"]
    stats = {
        "scenario": scen,
        "sessions": [],
        "created": 0,
        "superseded": 0,
        "contradicted": 0,
        "duplicates": 0,
    }
    writer = GraphWriter(db)
    event_store = SourceEventStore(db)
    for session_index, session in enumerate(doc["sessions"]):
        for message in session["messages"]:
            event = {
                "source_kind": message["source_kind"],
                "author": message["author"],
                "occurred_at": message["ts"],
                "content": message["text"],
                "source_id": message["msg_id"],
            }
            capture = event_store.capture(event)
            event_key = capture["event_key"]
            print(f"{event_key}: {capture['status']}")
            attempt = event_store.start_extraction(
                event_key,
                "openai-compatible",
                os.environ.get("LLM_MODEL", "kimi-k2"),
                "extract-v1",
            )
            extraction_key = attempt["extraction_key"]
            active = fetch_active_claims(db)
            one_message = {**session, "messages": [message]}
            try:
                drafts, warnings = extract_session(one_message, doc["entities"], active)
            except Exception as exc:
                event_store.fail_extraction(extraction_key, "EXTRACT", exc)
                raise
            for warning in warnings:
                print(f"warn [{message['msg_id']}]: {warning}", file=sys.stderr)
            try:
                plan = plan_writes(
                    drafts,
                    active,
                    doc["entities"],
                    id_prefix=f"{scen}:{message['msg_id']}",
                )
            except Exception as exc:
                event_store.fail_extraction(extraction_key, "RECONCILE", exc)
                raise
            try:
                applied = writer.apply_plan(
                    plan,
                    scen,
                    doc["entities"],
                    extraction_key=extraction_key,
                    source_event_keys={message["msg_id"]: event_key},
                )
            except Exception as exc:
                event_store.fail_extraction(extraction_key, "WRITE", exc)
                raise
            event_store.complete_extraction(
                extraction_key, [draft["id"] for draft in plan["create"]]
            )
            stats["sessions"].append(
                {
                    "session": session["session_id"],
                    "event": event_key,
                    "drafts": len(drafts),
                    **{
                        key: applied[key]
                        for key in (
                            "created",
                            "superseded",
                            "contradicted",
                            "duplicates",
                        )
                    },
                }
            )
            for key in ("created", "superseded", "contradicted", "duplicates"):
                stats[key] += applied[key]
    return stats


def main(argv: Sequence[str] | None = None) -> int | None:
    from hydraclaim.config import command_epilog

    parser = argparse.ArgumentParser(
        prog="hydraclaim pipeline",
        epilog=command_epilog(hydradb=True, llm="required"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("scenarios", nargs="+", help="scenario JSON files")
    args = parser.parse_args(argv)

    from hydraclaim import config

    config.require_settings(hydradb=True, llm=True)

    documents = []
    for path in args.scenarios:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        documents.append(validate_pipeline_document(doc))

    from hydraclaim.config import connect

    with connect() as db:
        for doc in documents:
            print(f"== {doc['scenario_id']} ==")
            run_pipeline(db, doc)


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("pipeline", main))
