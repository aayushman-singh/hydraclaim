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

from datetime import datetime

from hydraclaim.claim_read import (
    ClaimReader,
    ClaimScope,
    abstain_message,
    abstain_uncovered_message,
    build_chain_answer,
    build_conflict_answer,
    build_fast_answer,
    build_temporal_answer,
)
from hydraclaim.db import HydraDB


__all__ = [
    "abstain_message",
    "abstain_uncovered_message",
    "build_chain_answer",
    "build_conflict_answer",
    "build_fast_answer",
    "build_temporal_answer",
    "fetch_chain",
    "fetch_claims",
    "fetch_entities",
    "answer",
]


def fetch_entities(db: HydraDB) -> list[dict]:
    return [
        {"name": entity["name"], "aliases": entity["aliases"]}
        for entity in ClaimReader(db).read_entities()
    ]


def fetch_claims(
    db: HydraDB,
    subject: str,
    predicate: str | None,
    *,
    active_only: bool = False,
    as_of: str | None = None,
    limit: int = 25,
) -> list[dict]:
    return [
        claim.__dict__.copy()
        for claim in ClaimReader(db).read_claims(
            ClaimScope(
                subject=subject,
                predicate=predicate,
                active_only=active_only,
                as_of=as_of,
                limit=limit,
            )
        )
    ]


def fetch_chain(
    db: HydraDB, claim_id: int, subject: str, predicate: str | None = None
) -> list[dict]:
    """Read one bounded supersession chain."""
    return list(
        ClaimReader(db).read_chain(
            claim_id, ClaimScope(subject=subject, predicate=predicate)
        )
    )


# --- compatibility orchestration -------------------------------------------


def answer(
    db: HydraDB,
    question: str,
    *,
    now: datetime | None = None,
    force_route: str | None = None,
    classification_mode: str = "heuristic",
    llm_fn=None,
) -> dict:
    return (
        ClaimReader(db)
        .answer(
            question,
            classification_mode=classification_mode,
            llm_fn=llm_fn,
            now=now,
            force_route=force_route,
        )
        .as_dict()
    )
