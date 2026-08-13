"""Deterministic reconciliation: claim drafts -> graph write plan.

The LLM extracts and links explicit overwrites; everything else about graph
consistency is decided here, by rules that can be unit-tested offline:

1. Explicit supersession (draft.supersedes) always wins — the old claim
   closes (valid_to = draft.valid_from, status superseded).
2. Exact duplicate of an active claim (subject, predicate, value) -> skip.
3. Same subject+predicate, different value, SAME source_kind, not older:
   a source correcting itself -> SUPERSEDES.
4. Same subject+predicate, different value, DIFFERENT source_kind, no
   supersession signal -> CONTRADICTS {resolved: false}; both stay active.
   This is the unresolved-conflict case that drives deep retrieval.
5. Otherwise: plain new claim.

Value comparisons normalize case/whitespace. Draft ids supplied by the
caller (`id` key) are honored; missing ids are assigned `{id_prefix}:x{N}`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from trustgraph.cypher import to_cypher_literal as lit
from trustgraph.db import HydraDB
from trustgraph.ingest import _unwind, slug


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def canonicalize_entity(name: str, roster: list[dict]) -> str:
    low = name.strip().lower()
    for entity in roster:
        if low == entity["name"].strip().lower():
            return entity["name"]
        if low in [a.strip().lower() for a in entity.get("aliases", [])]:
            return entity["name"]
    return name.strip()


def _same_fact(claim: dict, subject: str, predicate: str, value: str) -> bool:
    return (
        _norm(claim["subject"]) == _norm(subject)
        and claim["predicate"] == predicate
        and _norm(claim["value"]) == _norm(value)
    )


def _relates(claim: dict, subject: str, predicate: str) -> bool:
    return _norm(claim["subject"]) == _norm(subject) and claim["predicate"] == predicate


def plan_writes(
    drafts: list[dict],
    active_claims: list[dict],
    roster: list[dict],
    id_prefix: str = "draft",
) -> dict:
    active = [dict(c, status=c.get("status", "active"), valid_to=c.get("valid_to"))
              for c in active_claims]
    by_id = {c["id"]: c for c in active}

    create: list[dict] = []
    supersede: list[dict] = []
    contradict: list[dict] = []
    contradict_pairs: set[tuple[str, str]] = set()
    warnings: list[str] = []
    duplicates = 0

    for n, draft in enumerate(drafts, start=1):
        cid = draft.get("id") or f"{id_prefix}:x{n}"
        subject = canonicalize_entity(draft["subject"], roster)
        enriched = {**draft, "id": cid, "subject": subject,
                    "status": "active", "valid_to": None}

        # Rule 1: explicit supersession.
        if draft.get("supersedes"):
            target = by_id.get(draft["supersedes"])
            if target is None:
                warnings.append(
                    f"{cid}: supersedes target {draft['supersedes']!r} is not an active "
                    "claim; ingested as a plain new claim"
                )
            else:
                supersede.append({"new_id": cid, "old_id": target["id"],
                                  "at": draft["valid_from"]})
                target["status"] = "superseded"
                target["valid_to"] = draft["valid_from"]
                create.append(enriched)
                active.append(dict(enriched))
                by_id[cid] = active[-1]
                continue

        # Rule 2: exact duplicate.
        if any(c["status"] == "active"
               and _same_fact(c, subject, draft["predicate"], draft["value"])
               for c in active):
            duplicates += 1
            continue

        # Rules 3+4: same fact slot, different value.
        conflicts = [
            c for c in active
            if c["status"] == "active"
            and _relates(c, subject, draft["predicate"])
            and _norm(c["value"]) != _norm(draft["value"])
        ]
        for c in conflicts:
            if c["source_kind"] == draft["source_kind"] and draft["valid_from"] >= c["valid_from"]:
                # Rule 3: a source correcting itself.
                supersede.append({"new_id": cid, "old_id": c["id"], "at": draft["valid_from"]})
                c["status"] = "superseded"
                c["valid_to"] = draft["valid_from"]
            else:
                # Rule 4: cross-source disagreement, no supersession signal.
                pair = tuple(sorted([cid, c["id"]]))
                if pair not in contradict_pairs:
                    contradict_pairs.add(pair)
                    contradict.append({"a_id": pair[0], "b_id": pair[1]})

        create.append(enriched)
        active.append(dict(enriched))
        by_id[cid] = active[-1]

    return {
        "create": create,
        "supersede": supersede,
        "contradict": contradict,
        "duplicates": duplicates,
        "warnings": warnings,
    }


def apply_plan(db: HydraDB, plan: dict, scenario_id: str) -> dict:
    """Write a plan into HydraDB (claims, evidence, sources, edges, closures)."""
    recorded_at = datetime.now(timezone.utc).isoformat()
    stats = {"scenario": scenario_id, "created": 0, "entities_created": 0,
             "superseded": len(plan["supersede"]), "contradicted": len(plan["contradict"]),
             "duplicates": plan["duplicates"], "warnings": plan["warnings"]}

    rows = []
    for draft in plan["create"]:
        cid = draft["id"]
        entity_id = f"{scenario_id}:{slug(draft['subject'])}"
        if not db.node_exists("Entity", entity_id):
            db.query(
                "CREATE (e:Entity {id: %s, name: %s, type: 'unknown', aliases: []})"
                % (lit(entity_id), lit(draft["subject"]))
            )
            stats["entities_created"] += 1
        rows.append({
            "cid": cid,
            "predicate": draft["predicate"],
            "value": draft["value"],
            "valid_from": draft["valid_from"],
            "valid_to": draft.get("valid_to"),
            "recorded_at": recorded_at,
            "status": draft.get("status", "active"),
            "confidence": draft["confidence"],
            "evid": f"{cid}:ev0",
            "quote": draft["quote"],
            "ts": draft["valid_from"],
            "session_id": draft["session_id"],
            "msg_id": draft["msg_id"],
            "extraction_confidence": draft["confidence"],
            "explicitness": draft["explicitness"],
            "entity_id": entity_id,
            "source_id": f"{draft['source_kind']}:{draft['author']}",
            "source_kind": draft["source_kind"],
            "source_author": draft["author"],
        })

    sources: dict[str, dict] = {}
    for row in rows:
        sources.setdefault(row["source_id"], {
            "id": row["source_id"], "kind": row["source_kind"], "author": row["source_author"],
        })
    for src in sources.values():
        if not db.node_exists("Source", src["id"]):
            db.query(
                "CREATE (s:Source {id: %s, kind: %s, author: %s})"
                % (lit(src["id"]), lit(src["kind"]), lit(src["author"]))
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
    _unwind(db, rows, """
MATCH (c:Claim {id: row.cid}), (e:Entity {id: row.entity_id})
CREATE (c)-[:ABOUT]->(e)""")
    _unwind(db, rows, """
MATCH (ev:Evidence {id: row.evid}), (s:Source {id: row.source_id})
CREATE (ev)-[:FROM]->(s)""")
    stats["created"] = len(rows)

    # Supersession edges + closure of the overwritten claims.
    _unwind(db, plan["supersede"], """
MATCH (new:Claim {id: row.new_id}), (old:Claim {id: row.old_id})
CREATE (new)-[:SUPERSEDES {at: row.at}]->(old)""")
    _unwind(db, plan["supersede"], """
MATCH (old:Claim {id: row.old_id})
SET old.valid_to = row.at, old.status = 'superseded'""")

    _unwind(db, [{**e, "detected_at": recorded_at} for e in plan["contradict"]], """
MATCH (a:Claim {id: row.a_id}), (b:Claim {id: row.b_id})
CREATE (a)-[:CONTRADICTS {resolved: false, detected_at: row.detected_at}]->(b)""")
    return stats
