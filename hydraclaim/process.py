"""Process one captured source event."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from hydraclaim.extract import extract_session
from hydraclaim.graph_write import GraphWriter
from hydraclaim.pipeline import fetch_active_claims
from hydraclaim.reconcile import plan_writes
from hydraclaim.source_event_read import read_event
from hydraclaim.source_events import SourceEventStore


def process_event(db, event_key: str, *, reprocess: bool = False) -> dict:
    """Run one explicit extraction attempt and stop at the first failed step."""
    source = read_event(db, event_key)["event"]
    store = SourceEventStore(db)
    attempt = store.start_extraction(
        event_key,
        "openai-compatible",
        os.environ.get("LLM_MODEL", "kimi-k2"),
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
    active = fetch_active_claims(db)
    try:
        drafts, _warnings = extract_session(session, [], active)
    except Exception as exc:
        store.fail_extraction(extraction_key, "EXTRACT", exc)
        raise
    try:
        plan = plan_writes(drafts, active, [], id_prefix=event_key)
    except Exception as exc:
        store.fail_extraction(extraction_key, "RECONCILE", exc)
        raise
    try:
        GraphWriter(db).apply_plan(
            plan,
            event_key,
            [],
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
    with connect() as db:
        print(json.dumps(process_event(db, args.event_key, reprocess=args.reprocess)))
    return 0


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("process", main))
