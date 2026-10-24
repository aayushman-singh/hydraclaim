"""Process one captured source event."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Sequence

from hydraclaim.extract import extract_session
from hydraclaim.graph_write import GraphWriter
from hydraclaim.pipeline import fetch_active_claims
from hydraclaim.reconcile import plan_writes
from hydraclaim.source_event_read import read_event
from hydraclaim.source_events import SourceEventStore, _content_hash
from hydraclaim.subject_context import (
    canonical_subject_context,
    subject_context_digest,
    validate_subject_context_source,
)


def _live_event_roster(author: object, active_claims: object) -> list[dict]:
    if author is not None and (not isinstance(author, str) or not author.strip()):
        raise ValueError("source author must be a non-empty string")
    if not isinstance(active_claims, list):
        raise ValueError("active claims must be a list")

    names_by_key: dict[str, str] = {}
    names: list[str] = []

    def add_active(name: object) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("live event entity names must be non-empty strings")
        normalized = name.strip()
        key = normalized.lower()
        existing = names_by_key.get(key)
        if existing is not None and existing != normalized:
            raise ValueError(
                f"active claim subjects have ambiguous canonical spelling: {key!r}"
            )
        if existing is None:
            names_by_key[key] = normalized
            names.append(normalized)

    for claim in active_claims:
        if not isinstance(claim, dict):
            raise ValueError("active claim must be an object")
        add_active(claim.get("subject"))

    if author is not None:
        author_name = author.strip()
        author_key = author_name.lower()
        if author_key not in names_by_key:
            names_by_key[author_key] = author_name
            names.append(author_name)

    return [{"name": name, "type": "unknown", "aliases": []} for name in names]


def _subject_context_roster(
    context: dict, active_claims: list[dict]
) -> tuple[list[dict], dict[str, str]]:
    active_roster = _live_event_roster(None, active_claims)
    entities = {entity["name"].lower(): entity for entity in active_roster}
    for subject in context["subjects"]:
        key = subject["name"].lower()
        entity = entities.get(key)
        if entity is None:
            entity = {
                "name": subject["name"],
                "type": subject["type"],
                "aliases": [],
            }
            entities[key] = entity
        else:
            entity["type"] = subject["type"]
        for alias in subject["aliases"]:
            alias_key = alias.lower()
            other = entities.get(alias_key)
            if other is not None and other is not entity:
                raise ValueError(f"subject context alias is ambiguous: {alias!r}")
            if alias not in entity["aliases"]:
                entity["aliases"].append(alias)
    roster = list(entities.values())
    subject_names = {
        subject["actor_ref"]: entities[subject["name"].lower()]["name"]
        for subject in context["subjects"]
    }
    return roster, subject_names


def _context_session(source: dict, context: dict, event_key: str, names: dict) -> dict:
    messages = []
    for message in context["messages"]:
        bound_name = names.get(message["actor_ref"])
        eligible = message["role"] == "user" and bound_name is not None
        speaker_name = (
            bound_name
            if eligible
            else "assistant"
            if message["role"] == "assistant"
            else "unresolved user"
        )
        messages.append(
            {
                "msg_id": message["msg_id"],
                "ts": message["ts"],
                "author": message["author"],
                "source_kind": source["source_kind"],
                "channel": "captured",
                "text": message["text"],
                "speaker_name": speaker_name,
                "role": message["role"],
                "eligible": eligible,
            }
        )
    return {
        "session_id": event_key,
        "started_at": source["occurred_at"],
        "messages": messages,
        "trusted_subject_context": True,
    }


def _validate_context_drafts(drafts: list[dict], session: dict) -> None:
    by_id = {message["msg_id"]: message for message in session["messages"]}
    for index, draft in enumerate(drafts):
        if not isinstance(draft, dict):
            raise ValueError(f"extracted claim {index} must be an object")
        message = by_id.get(draft.get("msg_id"))
        if message is None or not message["eligible"]:
            raise ValueError(
                f"extracted claim {index} must cite an eligible verified user message"
            )
        if draft.get("author") != message["author"]:
            raise ValueError(
                f"extracted claim {index} author does not match source message"
            )
        if draft.get("source_kind") != message["source_kind"]:
            raise ValueError(
                f"extracted claim {index} source kind does not match message"
            )
        if draft.get("session_id") != session["session_id"]:
            raise ValueError(f"extracted claim {index} session does not match source")
        quote = draft.get("quote")
        if not isinstance(quote, str) or not quote or quote not in message["text"]:
            raise ValueError(f"extracted claim {index} quote is not exact source text")


def process_event(
    db,
    event_key: str,
    *,
    extractor: Callable[[dict, list[dict], list[dict]], tuple[list[dict], list[str]]],
    provider: str,
    model: str,
    reprocess: bool = False,
    subject_context: dict | None = None,
) -> dict:
    """Run one explicit extraction attempt and stop at the first failed step."""
    source = read_event(db, event_key, include_private_context=True)["event"]
    store = SourceEventStore(db)
    captured_context = source.get("subject_context")
    captured_context_present = bool(
        source.get("subject_context_present", False)
        or "subject_context" in source
        or source.get("subject_context_digest") is not None
    )
    supplied_context = subject_context
    provenance = "captured"
    raw_context = captured_context
    if supplied_context is not None:
        if not captured_context_present:
            raw_context = supplied_context
            provenance = "operator_attestation"
    start_options = {}
    if captured_context_present or raw_context is not None:
        digest = source.get("subject_context_digest")
        if digest is None and provenance == "operator_attestation":
            try:
                digest = subject_context_digest(raw_context)
            except (TypeError, ValueError):
                digest = hashlib.sha256(
                    json.dumps(raw_context, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
        start_options = {
            "subject_context_digest": digest,
            "subject_context_provenance": provenance,
        }
        if provenance == "operator_attestation":
            start_options["subject_context_json"] = json.dumps(
                raw_context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
    attempt = store.start_extraction(
        event_key,
        provider,
        model,
        "extract-v1",
        reprocess=reprocess,
        **start_options,
    )
    extraction_key = attempt["extraction_key"]
    session = None
    try:
        if captured_context_present and raw_context is None:
            raise ValueError("captured subject context is missing or invalid")
        context = (
            validate_subject_context_source(raw_context, source)
            if raw_context is not None
            else None
        )
        if captured_context_present:
            if supplied_context is not None and canonical_subject_context(
                supplied_context
            ) != canonical_subject_context(context):
                raise ValueError(
                    "operator context cannot replace captured subject context"
                )
            if source.get("subject_context_digest") != subject_context_digest(context):
                raise ValueError(
                    "captured subject context digest failed integrity check"
                )
            if _content_hash(
                {
                    "source_kind": source["source_kind"],
                    "author": source["author"],
                    "occurred_at": source["occurred_at"],
                    "content": source["content"],
                    "subject_context": context,
                }
            ) != source.get("content_hash"):
                raise ValueError(
                    "captured source event content or context failed integrity check"
                )
        if context is not None:
            known_actor_refs = {subject["actor_ref"] for subject in context["subjects"]}
            unbound_users = [
                message["actor_ref"]
                for message in context["messages"]
                if message["role"] == "user"
                and message["actor_ref"] not in known_actor_refs
            ]
            if unbound_users:
                raise ValueError(
                    "subject context contains an unbound user actor: "
                    + ", ".join(sorted(set(unbound_users)))
                )
        active = fetch_active_claims(db)
        if context is not None:
            roster, subject_names = _subject_context_roster(context, active)
            session = _context_session(source, context, event_key, subject_names)
        else:
            roster = _live_event_roster(source.get("author"), active)
            session = {
                "session_id": event_key,
                "started_at": source["occurred_at"],
                "messages": [
                    {
                        "msg_id": event_key,
                        "ts": source["occurred_at"],
                        "author": source["author"],
                        "source_kind": source["source_kind"],
                        "channel": "captured",
                        "text": source["content"],
                    }
                ],
            }
        drafts, warnings = extractor(session, roster, active)
        if warnings:
            raise RuntimeError(f"extractor warnings prevent claim write: {warnings!r}")
        if context is not None:
            _validate_context_drafts(drafts, session)
        roster_names = {entity["name"] for entity in roster}
        for index, draft in enumerate(drafts):
            subject = draft.get("subject") if isinstance(draft, dict) else None
            if not isinstance(subject, str) or subject not in roster_names:
                raise ValueError(
                    f"extracted claim {index} subject is not an exact roster name"
                )
    except Exception as exc:
        store.fail_extraction(extraction_key, "EXTRACT", exc)
        raise
    try:
        plan = plan_writes(drafts, active, roster, id_prefix=event_key)
    except Exception as exc:
        store.fail_extraction(extraction_key, "RECONCILE", exc)
        raise
    try:
        GraphWriter(db).apply_plan(
            plan,
            event_key,
            roster,
            extraction_key=extraction_key,
            source_event_keys={event_key: event_key},
        )
    except Exception as exc:
        store.fail_extraction(extraction_key, "WRITE", exc)
        raise
    claim_keys = [draft["id"] for draft in plan["create"]]
    return store.complete_extraction(extraction_key, claim_keys)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydraclaim process")
    parser.add_argument("event_key")
    parser.add_argument("--reprocess", action="store_true")
    args = parser.parse_args(argv)
    from hydraclaim.config import connect, require_settings

    require_settings(hydradb=True, llm=True)
    model = os.environ["LLM_MODEL"].strip()
    with connect() as db:
        print(
            json.dumps(
                process_event(
                    db,
                    args.event_key,
                    extractor=extract_session,
                    provider="openai-compatible",
                    model=model,
                    reprocess=args.reprocess,
                )
            )
        )
    return 0


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("process", main))
