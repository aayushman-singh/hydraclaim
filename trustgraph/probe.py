"""Graph probe: cheap bounded queries that measure graph state for routing.

The probe is what makes routing defensible: decisions are driven by measured
graph state (coverage, conflict count, distinct active values, supersession
depth), not by question phrasing. Probe queries use no LLM and return counts,
so the routing cost is a handful of indexed lookups.

Note on conflicts: each CONTRADICTS edge is created once (sorted pair) but an
undirected MATCH sees it from both ends, so `count(r)` is halved.
"""

from __future__ import annotations

from dataclasses import dataclass

from trustgraph.cypher import to_cypher_literal as lit
from trustgraph.db import HydraDB


@dataclass
class ProbeResult:
    subject: str
    predicate: str | None
    coverage: int                  # claims for (subject[, predicate]), any status
    conflicts: int                 # unresolved CONTRADICTS edges on active claims
    distinct_active_values: int    # >1 means active disagreement even without edges
    chain_depth: int               # longest SUPERSEDES chain among matching claims


def _where(subject: str, predicate: str | None, active_only: bool = False) -> str:
    clauses = [f"(e.name = {lit(subject)} OR {lit(subject)} IN e.aliases)"]
    if predicate:
        clauses.append(f"c.predicate = {lit(predicate)}")
    if active_only:
        clauses.append("c.status = 'active'")
    return " AND ".join(clauses)


def probe(db: HydraDB, subject: str, predicate: str | None) -> ProbeResult:
    where = _where(subject, predicate)

    row = db.query_one(
        f"MATCH (c:Claim)-[:ABOUT]->(e:Entity) WHERE {where} RETURN count(c) AS n"
    )
    coverage = int((row or {}).get("n", 0))

    conflicts = 0
    distinct_values = 0
    chain_depth = 0
    if predicate and coverage:
        active_where = _where(subject, predicate, active_only=True)
        row = db.query_one(f"""
MATCH (a:Claim)-[:ABOUT]->(e:Entity)
WHERE {active_where.replace('c.', 'a.')}
MATCH (a)-[r:CONTRADICTS]-(b:Claim)
WHERE r.resolved = false
RETURN count(r) AS n""")
        conflicts = int((row or {}).get("n", 0)) // 2

        rows = db.query(f"""
MATCH (c:Claim)-[:ABOUT]->(e:Entity)
WHERE {active_where}
RETURN c.value AS value""")
        distinct_values = len({str(r.get("value", "")).strip().lower() for r in rows})

        rows = db.query(f"""
MATCH (newer:Claim)-[:ABOUT]->(e:Entity)
WHERE {_where(subject, predicate).replace('c.', 'newer.')}
MATCH p = (newer)-[:SUPERSEDES*1..5]->(older:Claim)
RETURN length(p) AS hops""")
        chain_depth = max((int(r["hops"]) for r in rows), default=0)

    return ProbeResult(
        subject=subject,
        predicate=predicate,
        coverage=coverage,
        conflicts=conflicts,
        distinct_active_values=distinct_values,
        chain_depth=chain_depth,
    )
