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
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from hydraclaim.db import HydraDB
from hydraclaim.extract import extract_session
from hydraclaim.graph_write import GraphWriter
from hydraclaim.reconcile import plan_writes


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
        row["source_kind"] = row.get("source_kind") or "unknown"
        row["author"] = row.get("author") or "unknown"
    return rows


def run_pipeline(
    db: HydraDB,
    doc: dict,
    step_hook: Callable[..., None] | None = None,
) -> dict:
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
    for session_index, session in enumerate(doc["sessions"]):
        if step_hook:
            step_hook(
                "read_active",
                session_index=session_index,
                session_count=len(doc["sessions"]),
            )
        active = fetch_active_claims(db)
        if step_hook:
            step_hook(
                "extract",
                session_index=session_index,
                session_count=len(doc["sessions"]),
                active_count=len(active),
            )
        drafts, warnings = extract_session(session, doc["entities"], active)
        for warning in warnings:
            print(f"warn [{session['session_id']}]: {warning}", file=sys.stderr)
        if step_hook:
            step_hook(
                "reconcile",
                session_index=session_index,
                session_count=len(doc["sessions"]),
                active_count=len(active),
                draft_count=len(drafts),
            )
        plan = plan_writes(
            drafts, active, doc["entities"], id_prefix=f"{scen}:{session['session_id']}"
        )
        for warning in plan["warnings"]:
            print(f"warn [{session['session_id']}]: {warning}", file=sys.stderr)
        if step_hook:
            step_hook(
                "graph_write",
                session_index=session_index,
                session_count=len(doc["sessions"]),
                draft_count=len(drafts),
                plan_create_count=len(plan["create"]),
                plan_supersede_count=len(plan["supersede"]),
                plan_contradict_count=len(plan["contradict"]),
                plan_duplicate_count=plan["duplicates"],
                applied=False,
            )
        applied = writer.apply_plan(plan, scen, doc["entities"])
        stats["sessions"].append(
            {
                "session": session["session_id"],
                "drafts": len(drafts),
                "created": applied["created"],
                "superseded": applied["superseded"],
                "contradicted": applied["contradicted"],
                "duplicates": applied["duplicates"],
            }
        )
        stats["created"] += applied["created"]
        stats["superseded"] += applied["superseded"]
        stats["contradicted"] += applied["contradicted"]
        stats["duplicates"] += applied["duplicates"]
        print(
            f"{session['session_id']}: {applied['created']} created, "
            f"{applied['superseded']} superseded, "
            f"{applied['contradicted']} contradicted, "
            f"{applied['duplicates']} duplicate(s)"
        )
    return stats


def main(argv: Sequence[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(prog="hydraclaim pipeline")
    parser.add_argument("scenarios", nargs="+", help="scenario JSON files")
    args = parser.parse_args(argv)

    from hydraclaim.config import connect

    with connect() as db:
        for path in args.scenarios:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            print(f"== {doc['scenario_id']} ==")
            run_pipeline(db, doc)


if __name__ == "__main__":
    raise SystemExit(main())
