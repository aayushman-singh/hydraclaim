"""Retrieval paths and answer composition.

Three routes (chosen by router.decide_route over probe.probe):
- FAST: one bounded lookup of the latest active claim -> short cited answer.
- DEEP: pull the conflict subgraph (all claims, evidence, sources,
  supersession chain) -> timeline / conflict answer with trust scores.
- ABSTAIN: coverage is zero -> decline and say what was searched.

Answers are composed deterministically so the system is fully demoable
without an LLM at query time; every claim used comes back in `citations`.

Dialect notes (verified D1): aliases are pipe-delimited strings split
client-side; open validity is `valid_to = ''` (IS NULL unsupported); chain
history uses a single-type varlen path and client-side ordering, since
`length(p)` is unsupported.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone

from trustgraph.cypher import to_cypher_literal as lit
from trustgraph.db import HydraDB
from trustgraph.model import split_aliases
from trustgraph.probe import probe
from trustgraph.router import (
    ROUTE_ABSTAIN,
    ROUTE_DEEP,
    ROUTE_FAST,
    Classification,
    classify,
    decide_route,
)
from trustgraph.scoring import rank_claims


def fetch_entities(db: HydraDB) -> list[dict]:
    rows = db.query("MATCH (e:Entity) RETURN e.name AS name, e.aliases AS aliases")
    return [{"name": r["name"], "aliases": split_aliases(r.get("aliases"))} for r in rows]


def fetch_claims(
    db: HydraDB,
    subject: str,
    predicate: str | None,
    *,
    active_only: bool = False,
    as_of: str | None = None,
    limit: int = 25,
) -> list[dict]:
    clauses = [f"e.name = {lit(subject)}"]
    if predicate:
        clauses.append(f"c.predicate = {lit(predicate)}")
    if active_only:
        clauses.append("c.status = 'active'")
    if as_of:
        clauses.append(
            f"(c.recorded_at <= {lit(as_of)} "
            f"AND (c.valid_to = '' OR c.valid_to > {lit(as_of)}))"
        )
    return db.query(f"""
MATCH (c:Claim)-[:ABOUT]->(e:Entity)
WHERE {" AND ".join(clauses)}
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(s:Source)
RETURN c.id AS id, c.key AS key, e.name AS subject, c.predicate AS predicate,
       c.value AS value,
       c.valid_from AS valid_from, c.valid_to AS valid_to, c.status AS status,
       c.confidence AS confidence,
       ev.quote AS quote, ev.explicitness AS explicitness,
       ev.extraction_confidence AS extraction_confidence,
       s.kind AS source_kind, s.author AS author
ORDER BY c.valid_from DESC
LIMIT {int(limit)}""")


def fetch_chain(db: HydraDB, claim_id: int) -> list[dict]:
    """Supersession history behind a claim, nearest ancestor first (client-side
    ordering by valid_from — `length(p)` is unsupported in this dialect)."""
    rows = db.query(f"""
MATCH p = (c:Claim {{id: {int(claim_id)}}})-[:SUPERSEDES*1..5]->(older:Claim)
RETURN older.id AS id, older.value AS value,
       older.valid_from AS valid_from, older.valid_to AS valid_to""")
    rows.sort(key=lambda r: r["valid_from"], reverse=True)
    return rows


# --- deterministic answer builders (pure, unit-tested) ----------------------

def _citation(claim: dict) -> dict:
    return {
        "claim_id": claim.get("key") or claim["id"],
        "value": claim["value"],
        "valid_from": claim["valid_from"],
        "valid_to": claim.get("valid_to"),
        "source_kind": claim.get("source_kind"),
        "author": claim.get("author"),
        "quote": claim.get("quote"),
    }


def abstain_message(subject: str, predicate: str | None) -> str:
    if predicate:
        return (f"I don't have any recorded information about the {predicate} of "
                f"'{subject}'. I searched claims about '{subject}' with predicate "
                f"'{predicate}' and found none — the answer is not in the history.")
    return (f"I don't have any recorded information about '{subject}'. I searched "
            f"all claims about that entity and found none.")


def abstain_uncovered_message(subject: str, available: list[str]) -> str:
    """Abstain when the question maps to no tracked predicate but the subject
    has claims: say what the graph CAN answer about the subject."""
    if not available:
        return abstain_message(subject, None)
    tracked = ", ".join(available)
    return (f"I don't have a recorded fact that answers that about '{subject}'. "
            f"The claims I track for '{subject}' cover: {tracked} — none of those "
            f"match the question, so the answer is not in the history.")


# Origin questions ("when was X first set?") ask for the earliest claim in the
# history, not the latest active one.
_ORIGIN_RE = re.compile(r"\b(first|originally|initially|earliest)\b", re.IGNORECASE)


def build_fast_answer(claim: dict) -> str:
    return (
        f"{claim['subject']} — {claim['predicate']}: {claim['value']} "
        f"(as of {claim['valid_from']}, per {claim.get('source_kind')}/"
        f"{claim.get('author')}: \"{claim.get('quote')}\")"
    )


def build_chain_answer(head: dict, chain: list[dict]) -> str:
    lines = [
        f"{head['subject']} — {head['predicate']}: {head['value']} "
        f"(current, since {head['valid_from']}).",
        "Previously:",
    ]
    for ancestor in chain:
        lines.append(
            f"  - {ancestor['value']} ({ancestor['valid_from']} -> "
            f"{ancestor['valid_to'] or '?'})"
        )
    return "\n".join(lines)


def build_conflict_answer(
    subject: str, predicate: str, ranked: list[tuple[dict, float]]
) -> str:
    lines = [f"Unresolved conflict about {subject} — {predicate}:"]
    for claim, score in ranked:
        lines.append(
            f"  - {claim['value']} — {claim.get('source_kind')}/{claim.get('author')}, "
            f"{claim['valid_from']} (trust {score:.2f}): \"{claim.get('quote')}\""
        )
    winner = ranked[0][0]
    lines.append(
        f"The highest-trust record says {winner['value']}, but the conflicting "
        f"records were never reconciled."
    )
    return "\n".join(lines)


# --- orchestration ----------------------------------------------------------

def answer(
    db: HydraDB,
    question: str,
    *,
    now: datetime | None = None,
    force_route: str | None = None,
    llm_fn=None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    roster = fetch_entities(db)
    cls: Classification = classify(question, roster, llm_fn=llm_fn, now=now)

    if cls.subject is None:
        return {"route": ROUTE_ABSTAIN, "answer": abstain_message(question, None),
                "citations": [], "classification": asdict(cls), "probe": None}

    p = probe(db, cls.subject, cls.predicate)
    route = force_route or decide_route(cls.question_type, p)
    base = {"classification": asdict(cls), "probe": asdict(p)}

    if route == ROUTE_ABSTAIN:
        if cls.predicate is None and p.coverage:
            active_any = fetch_claims(db, cls.subject, None, active_only=True)
            available = sorted({r["predicate"] for r in active_any})
            msg = abstain_uncovered_message(cls.subject, available)
        else:
            msg = abstain_message(cls.subject, cls.predicate)
        return {**base, "route": route, "answer": msg, "citations": []}

    active = fetch_claims(db, cls.subject, cls.predicate, active_only=True,
                          as_of=cls.as_of)
    if not active:
        return {**base, "route": ROUTE_ABSTAIN,
                "answer": (abstain_message(cls.subject, cls.predicate) +
                           " (Claims exist in history, but none are currently active"
                           + (f" as of {cls.as_of}." if cls.as_of else ".")),
                "citations": []}

    if cls.predicate and _ORIGIN_RE.search(question):
        history = fetch_claims(db, cls.subject, cls.predicate)
        if history:
            oldest = min(history,
                         key=lambda r: (r["valid_from"], str(r.get("key") or r["id"])))
            return {**base, "route": route,
                    "answer": build_fast_answer(oldest),
                    "citations": [_citation(oldest)]}

    if route == ROUTE_FAST:
        return {**base, "route": route, "answer": build_fast_answer(active[0]),
                "citations": [_citation(active[0])]}

    # DEEP: pull the full conflict subgraph.
    conflicts = p.conflicts > 0 or p.distinct_active_values > 1
    if conflicts:
        ranked = rank_claims(active, cls.predicate or "", now)
        return {**base, "route": route,
                "answer": build_conflict_answer(cls.subject, cls.predicate or "", ranked),
                "citations": [_citation(c) for c, _ in ranked]}
    chain = fetch_chain(db, active[0]["id"])
    if chain:
        return {**base, "route": route,
                "answer": build_chain_answer(active[0], chain),
                "citations": [_citation(active[0])]}
    return {**base, "route": route, "answer": build_fast_answer(active[0]),
            "citations": [_citation(active[0])]}
