"""End-to-end ingestion: scenario JSON -> LLM extraction -> reconcile -> HydraDB.

Per session: extract claims (with the current active claims as context),
plan the writes deterministically, apply them, then re-read active claims
from the graph so the next session's prompt sees real graph ids.

CLI: python -m trustgraph.pipeline SCENARIO_JSON [...]
Requires a live HydraDB node (scripts/dev-up.sh) and LLM_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trustgraph.db import HydraDB
from trustgraph.extract import extract_session
from trustgraph.reconcile import apply_plan, plan_writes


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


def run_pipeline(db: HydraDB, doc: dict) -> dict:
    scen = doc["scenario_id"]
    stats = {"scenario": scen, "sessions": [], "created": 0,
             "superseded": 0, "contradicted": 0, "duplicates": 0}
    for session in doc["sessions"]:
        active = fetch_active_claims(db)
        drafts, warnings = extract_session(session, doc["entities"], active)
        for warning in warnings:
            print(f"warn [{session['session_id']}]: {warning}", file=sys.stderr)
        plan = plan_writes(drafts, active, doc["entities"],
                           id_prefix=f"{scen}:{session['session_id']}")
        for warning in plan["warnings"]:
            print(f"warn [{session['session_id']}]: {warning}", file=sys.stderr)
        applied = apply_plan(db, plan, scen, doc["entities"])
        stats["sessions"].append({
            "session": session["session_id"],
            "drafts": len(drafts),
            "created": applied["created"],
            "superseded": applied["superseded"],
            "contradicted": applied["contradicted"],
            "duplicates": applied["duplicates"],
        })
        stats["created"] += applied["created"]
        stats["superseded"] += applied["superseded"]
        stats["contradicted"] += applied["contradicted"]
        stats["duplicates"] += applied["duplicates"]
        print(f"{session['session_id']}: {applied['created']} created, "
              f"{applied['superseded']} superseded, "
              f"{applied['contradicted']} contradicted, "
              f"{applied['duplicates']} duplicate(s)")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(prog="trustgraph.pipeline")
    parser.add_argument("scenarios", nargs="+", help="scenario JSON files")
    args = parser.parse_args()

    from trustgraph.config import connect

    with connect() as db:
        for path in args.scenarios:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            print(f"== {doc['scenario_id']} ==")
            run_pipeline(db, doc)


if __name__ == "__main__":
    main()
