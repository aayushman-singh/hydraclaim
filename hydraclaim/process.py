"""Process one captured source event."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence

from hydraclaim.extract import extract_session
from hydraclaim.graph_write import GraphWriter
from hydraclaim.pipeline import fetch_active_claims
from hydraclaim.reconcile import plan_writes
from hydraclaim.source_event_read import read_event
from hydraclaim.source_events import SourceEventStore


def _live_event_roster(author: object, active_claims: object) -> list[dict]:
    if not isinstance(author, str) or not author.strip():
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

    author_name = author.strip()
    author_key = author_name.lower()
    if author_key not in names_by_key:
        names_by_key[author_key] = author_name
        names.append(author_name)

    return [{"name": name, "type": "unknown", "aliases": []} for name in names]


def process_event(
    db,
    event_key: str,
    *,
    extractor: Callable[[dict, list[dict], list[dict]], tuple[list[dict], list[str]]],
    provider: str,
    model: str,
    reprocess: bool = False,
) -> dict:
    """Run one explicit extraction attempt and stop at the first failed step."""
    source = read_event(db, event_key)["event"]
    store = SourceEventStore(db)
    attempt = store.start_extraction(
        event_key,
        provider,
        model,
        "extract-v1",
        reprocess=reprocess,
    )
    extraction_key = attempt["extraction_key"]
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
    try:
        active = fetch_active_claims(db)
        roster = _live_event_roster(source.get("author"), active)
        drafts, warnings = extractor(session, roster, active)
        if warnings:
            raise RuntimeError(f"extractor warnings prevent claim write: {warnings!r}")
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
