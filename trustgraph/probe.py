"""Graph probe: cheap bounded queries that measure graph state for routing.

The probe is what makes routing defensible: decisions are driven by measured
graph state (coverage, conflict count, distinct active values, supersession
depth), not by question phrasing.

Dialect notes (verified D1): undirected edge matches are unsupported, so
CONTRADICTS edges are read directed (they are created exactly once per pair);
`length(p)` and `max()` don't exist, so chain depth is computed client-side
from the SUPERSEDES edge list. Edge lists are scanned whole-graph — fine at
demo/benchmark scale (thousands of edges); page by label if that changes.
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
    conflicts: int                 # unresolved CONTRADICTS edges touching active claims
    distinct_active_values: int    # >1 means active disagreement even without edges
    chain_depth: int               # longest SUPERSEDES chain among matching claims


def _chain_depth(edges: list[tuple[int, int]], ids: set[int]) -> int:
    """Longest path length (in edges) through the SUPERSEDES DAG, client-side."""
    forward: dict[int, list[int]] = {}
    for new_id, old_id in edges:
        if new_id in ids and old_id in ids:
            forward.setdefault(new_id, []).append(old_id)

    memo: dict[int, int] = {}

    def depth(node: int) -> int:
        if node not in memo:
            memo[node] = max((1 + depth(child) for child in forward.get(node, [])),
                             default=0)
        return memo[node]

    return max((depth(n) for n in forward), default=0)


def probe(db: HydraDB, subject: str, predicate: str | None) -> ProbeResult:
    clauses = [f"e.name = {lit(subject)}"]
    if predicate:
        clauses.append(f"c.predicate = {lit(predicate)}")
    rows = db.query(f"""
MATCH (c:Claim)-[:ABOUT]->(e:Entity)
WHERE {" AND ".join(clauses)}
RETURN c.id AS id, c.status AS status, c.value AS value""")

    coverage = len(rows)
    active_ids = {int(r["id"]) for r in rows if r.get("status") == "active"}
    distinct_values = len(
        {str(r.get("value", "")).strip().lower() for r in rows if int(r["id"]) in active_ids}
    )

    conflicts = 0
    chain_depth = 0
    if predicate and coverage:
        claim_ids = {int(r["id"]) for r in rows}
        sup_edges = [
            (int(r["new_id"]), int(r["old_id"]))
            for r in db.query(
                "MATCH (a:Claim)-[:SUPERSEDES]->(b:Claim) "
                "RETURN a.id AS new_id, b.id AS old_id"
            )
        ]
        chain_depth = _chain_depth(sup_edges, claim_ids)

        con_rows = db.query(
            "MATCH (a:Claim)-[r:CONTRADICTS]->(b:Claim) "
            "RETURN a.id AS a_id, b.id AS b_id, r.resolved AS resolved"
        )
        conflicts = len({
            tuple(sorted((int(r["a_id"]), int(r["b_id"]))))
            for r in con_rows
            if not r.get("resolved", False)
            and (int(r["a_id"]) in active_ids or int(r["b_id"]) in active_ids)
        })

    return ProbeResult(
        subject=subject,
        predicate=predicate,
        coverage=coverage,
        conflicts=conflicts,
        distinct_active_values=distinct_values,
        chain_depth=chain_depth,
    )
