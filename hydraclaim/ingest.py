"""Write a generated scenario document into HydraDB as the claim/evidence graph.

This is the deterministic ingestion path used for development and for the
benchmark's oracle arm: ground-truth claims go through the same graph-write
code that the LLM extraction pipeline (reconcile.apply_plan) uses.

Write-path dialect (verified live, D1): every statement is a single one-hop
CREATE whose endpoints upsert by integer `id`. Nodes are skipped when they
already exist, so re-ingesting a document is safe. Edges are created only
between id-known endpoints.

CLI: python -m hydraclaim.ingest data/sessions/deadline_drift.json [...]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from hydraclaim.cypher import to_cypher_literal as lit
from hydraclaim.db import HydraDB
from hydraclaim.model import (
    claim_props,
    entity_key,
    entity_props,
    evidence_props,
    graph_id,
    source_props,
)


def _props(props: dict) -> str:
    return "{" + ", ".join(f"{k}: {lit(v)}" for k, v in props.items()) + "}"


def node_exists(db: HydraDB, label: str, key: str) -> bool:
    return db.node_exists(label, graph_id(key))


def edge_exists(db: HydraDB, label_a: str, id_a: int, rel: str,
                label_b: str, id_b: int) -> bool:
    row = db.query_one(
        f"MATCH (a:{label_a} {{id: {id_a}}})-[:{rel}]->(b:{label_b} {{id: {id_b}}}) "
        "RETURN count(*) AS c"
    )
    return bool(row and row.get("c", 0) > 0)


def write_claim(db: HydraDB, cid: str, claim: dict, entity_endpoint: str,
                recorded_at: str) -> None:
    """Create one claim + evidence + source + entity attachment (3 CREATEs)."""
    cprops = _props(claim_props(claim, cid, recorded_at))
    eprops = _props(evidence_props(claim, cid))
    evid = evidence_props(claim, cid)["id"]
    sprops = _props(source_props(claim["source_kind"], claim["author"]))

    db.query(f"CREATE (c:Claim {cprops})-[:SUPPORTED_BY]->(ev:Evidence {eprops})")
    db.query(f"CREATE (ev:Evidence {{id: {evid}}})-[:FROM]->(s:Source {sprops})")
    db.query(f"CREATE (c:Claim {{id: {graph_id(cid)}}})-[:ABOUT]->(e:Entity {entity_endpoint})")


def write_edge(db: HydraDB, rel: str, a_key: str, b_key: str, props: dict) -> bool:
    """Create one relationship edge between two claim ids.

    Returns True when the edge was created, False when it already exists
    (idempotent re-ingest).
    """
    a_id, b_id = graph_id(a_key), graph_id(b_key)
    if edge_exists(db, "Claim", a_id, rel, "Claim", b_id):
        return False
    prop_str = f" {_props(props)}" if props else ""
    db.query(f"CREATE (a:Claim {{id: {a_id}}})-[:{rel}{prop_str}]->(b:Claim {{id: {b_id}}})")
    return True


def ingest_document(db: HydraDB, doc: dict) -> dict:
    scen = doc["scenario_id"]
    recorded_at = datetime.now(timezone.utc).isoformat()
    stats = {"scenario": scen, "claims": 0, "supersedes_edges": 0,
             "contradicts_edges": 0, "skipped_existing": 0, "warnings": []}

    claims = doc["ground_truth"]["claims"]
    claimed_subjects = {c["subject"] for c in claims}
    for entity in doc["entities"]:
        if entity["name"] not in claimed_subjects:
            stats["warnings"].append(
                f"entity {entity['name']!r} has no claims; not created "
                "(nodes are created paired with their first claim)"
            )

    # Entities upsert with full props once; later claims attach by id only.
    entity_endpoint: dict[str, str] = {}
    for entity in doc["entities"]:
        if entity["name"] in claimed_subjects:
            entity_endpoint[entity["name"]] = _props(
                entity_props(scen, entity["name"], entity.get("type", "unknown"),
                             entity.get("aliases", []))
            )

    for claim in claims:
        cid = f"{scen}:{claim['key']}"
        if node_exists(db, "Claim", cid):
            stats["skipped_existing"] += 1
            continue
        subject = claim["subject"]
        endpoint = entity_endpoint.get(subject)
        if endpoint is None:
            endpoint = _props(entity_props(scen, subject))
        write_claim(db, cid, claim, endpoint, recorded_at)
        # After first attachment, reference the entity by id only (avoids
        # re-upserting full props on every claim).
        entity_endpoint[subject] = f"{{id: {graph_id(entity_key(scen, subject))}}}"
        stats["claims"] += 1

    for claim in claims:
        cid = f"{scen}:{claim['key']}"
        if claim.get("supersedes"):
            if write_edge(db, "SUPERSEDES", cid, f"{scen}:{claim['supersedes']}",
                          {"at": claim["valid_from"]}):
                stats["supersedes_edges"] += 1
        for other in claim.get("contradicts_with", []):
            pair = sorted([cid, f"{scen}:{other}"])
            if write_edge(db, "CONTRADICTS", pair[0], pair[1],
                          {"resolved": False, "detected_at": recorded_at}):
                stats["contradicts_edges"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(prog="hydraclaim.ingest")
    parser.add_argument("documents", nargs="+", help="scenario JSON files to ingest")
    args = parser.parse_args()

    from hydraclaim.config import connect

    with connect() as db:
        for path in args.documents:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            print(json.dumps(ingest_document(db, doc), indent=2))


if __name__ == "__main__":
    main()
