"""Write a generated scenario document into HydraDB as the claim/evidence graph.

This is the deterministic ingestion path used for development and for the
benchmark's oracle arm: ground-truth claims go through the same graph-write
code that the LLM extraction pipeline (D2) will feed, so switching to
extracted claims changes only where the claim list comes from, not how it
lands in the graph.

Idempotent by graph id: re-ingesting a document skips nodes that already
exist. Graph ids are namespaced as `{scenario_id}:{claim_key}` so scenarios
never collide.

CLI: python -m trustgraph.ingest data/sessions/deadline_drift.json [...]
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from trustgraph.cypher import to_cypher_literal as lit
from trustgraph.db import HydraDB


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _unwind(db: HydraDB, rows: list[dict], body: str) -> None:
    if rows:
        db.query(f"UNWIND {lit(rows)} AS row\n{body}")


def ingest_document(db: HydraDB, doc: dict) -> dict:
    scen = doc["scenario_id"]
    recorded_at = datetime.now(timezone.utc).isoformat()
    stats = {"scenario": scen, "entities": 0, "claims": 0, "supersedes_edges": 0,
             "contradicts_edges": 0, "skipped_existing": 0}

    # --- entities -------------------------------------------------------
    entity_ids: dict[str, str] = {}
    for entity in doc["entities"]:
        eid = f"{scen}:{slug(entity['name'])}"
        entity_ids[entity["name"]] = eid
        if db.node_exists("Entity", eid):
            stats["skipped_existing"] += 1
            continue
        db.query(
            "CREATE (e:Entity {id: %s, name: %s, type: %s, aliases: %s})"
            % (lit(eid), lit(entity["name"]), lit(entity["type"]), lit(entity["aliases"]))
        )
        stats["entities"] += 1

    # --- claims + evidence (batched) ------------------------------------
    claims = doc["ground_truth"]["claims"]
    rows = []
    for c in claims:
        cid = f"{scen}:{c['key']}"
        if db.node_exists("Claim", cid):
            stats["skipped_existing"] += 1
            continue
        rows.append(
            {
                "cid": cid,
                "predicate": c["predicate"],
                "value": c["value"],
                "valid_from": c["valid_from"],
                "valid_to": c["valid_to"],
                "recorded_at": recorded_at,
                "status": c["status"],
                "confidence": c["confidence"],
                "evid": f"{cid}:ev0",
                "quote": c["quote"],
                "ts": c["valid_from"],
                "session_id": c["session_id"],
                "msg_id": c["msg_id"],
                "extraction_confidence": c["confidence"],
                "explicitness": c["explicitness"],
                "entity_id": entity_ids[c["subject"]],
                "source_id": f"{c['source_kind']}:{c['author']}",
                "source_kind": c["source_kind"],
                "source_author": c["author"],
            }
        )

    _unwind(db, rows, """
CREATE (c:Claim {id: row.cid, predicate: row.predicate, value: row.value,
                 valid_from: row.valid_from, valid_to: row.valid_to,
                 recorded_at: row.recorded_at, status: row.status,
                 confidence: row.confidence})
CREATE (ev:Evidence {id: row.evid, quote: row.quote, ts: row.ts,
                     session_id: row.session_id, msg_id: row.msg_id,
                     extraction_confidence: row.extraction_confidence,
                     explicitness: row.explicitness})
CREATE (c)-[:SUPPORTED_BY]->(ev)""")

    # Sources: one node per distinct (kind, author), then edges to evidence.
    sources: dict[str, dict] = {}
    for row in rows:
        sources.setdefault(
            row["source_id"],
            {"id": row["source_id"], "kind": row["source_kind"], "author": row["source_author"]},
        )
    for src in sources.values():
        if db.node_exists("Source", src["id"]):
            continue
        db.query(
            "CREATE (s:Source {id: %s, kind: %s, author: %s})"
            % (lit(src["id"]), lit(src["kind"]), lit(src["author"]))
        )

    _unwind(db, rows, """
MATCH (c:Claim {id: row.cid}), (e:Entity {id: row.entity_id})
CREATE (c)-[:ABOUT]->(e)""")
    _unwind(db, rows, """
MATCH (ev:Evidence {id: row.evid}), (s:Source {id: row.source_id})
CREATE (ev)-[:FROM]->(s)""")
    stats["claims"] = len(rows)

    # --- supersession + contradiction edges ------------------------------
    sup_rows, con_rows = [], []
    for c in claims:
        if c["supersedes"]:
            sup_rows.append(
                {"new_id": f"{scen}:{c['key']}", "old_id": f"{scen}:{c['supersedes']}",
                 "at": c["valid_from"]}
            )
        for other in c["contradicts_with"]:
            pair = sorted([f"{scen}:{c['key']}", f"{scen}:{other}"])
            con_rows.append({"a_id": pair[0], "b_id": pair[1], "detected_at": recorded_at})

    _unwind(db, sup_rows, """
MATCH (new:Claim {id: row.new_id}), (old:Claim {id: row.old_id})
CREATE (new)-[:SUPERSEDES {at: row.at}]->(old)""")
    _unwind(db, con_rows, """
MATCH (a:Claim {id: row.a_id}), (b:Claim {id: row.b_id})
CREATE (a)-[:CONTRADICTS {resolved: false, detected_at: row.detected_at}]->(b)""")
    stats["supersedes_edges"] = len(sup_rows)
    stats["contradicts_edges"] = len(con_rows)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(prog="trustgraph.ingest")
    parser.add_argument("documents", nargs="+", help="scenario JSON files to ingest")
    args = parser.parse_args()

    from trustgraph.config import connect

    with connect() as db:
        for path in args.documents:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            print(json.dumps(ingest_document(db, doc), indent=2))


if __name__ == "__main__":
    main()
