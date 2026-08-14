"""Two-stage router: question classification, then graph-probe routing.

Stage 1 (classify): {subject, predicate, question_type, as_of}. Uses the LLM
when available, with a deterministic keyword heuristic as fallback — the
heuristic also makes the demo runnable with no LLM key at all. Conflict and
abstention are deliberately NOT inferred from wording; they come from the
probe (stage 2), because question phrasing is a weak predictor of graph state.

Stage 2 (decide_route), per PLAN.md:
    predicate is None (question maps to no tracked fact)   -> ABSTAIN
    coverage == 0                                          -> ABSTAIN
    conflicts == 0 AND distinct values <= 1 AND depth <= 1
      AND type == lookup                                   -> FAST
    otherwise                                              -> DEEP
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from trustgraph.claims import PREDICATES
from trustgraph.probe import ProbeResult
from trustgraph.reconcile import canonicalize_entity

ROUTE_FAST = "FAST"
ROUTE_DEEP = "DEEP"
ROUTE_ABSTAIN = "ABSTAIN"

QUESTION_TYPES = frozenset(
    {"lookup", "temporal", "conflict", "knowledge_update", "multi_session", "abstention"}
)

# Checked in order; first hit wins. Keep specific predicates above generic ones.
_PREDICATE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("owned_by", ("own", "owner", "who owns")),
    ("assigned_to", ("assign", "on the team", "which team", "joining the", "moved to the")),
    ("deadline", ("deadline", "due date", "due")),
    ("status", ("status", "at risk", "on track", "blocked", "blocker", "complete",
                "delayed", "green", "red")),
    ("decided", ("decid", "approved")),
    ("depends_on", ("depend", "blocked by", "waiting on")),
    ("blocks", ("blocks", "blocking")),
    ("reports_to", ("reports to", "manager")),
    ("works_on", ("working on", "works on")),
    ("located_in", ("located", "based in", "based", "where is", "where was", "where does")),
    ("prefers", ("prefer",)),
    ("budget", ("budget", "cost", "how much")),
]

_TEMPORAL_MARKERS = ("before", "previously", "used to", "originally",
                     "at the start", "earlier", "last week", "last month")
_UPDATE_MARKERS = ("current", "currently", "now", "latest", "today")


@dataclass
class Classification:
    subject: str | None      # canonical entity name, None if nothing matched
    predicate: str | None
    question_type: str
    as_of: str | None        # ISO date for time-travel questions


def _find_subject(question: str, roster: list[dict]) -> str | None:
    q = question.lower()
    candidates: list[tuple[int, str]] = []
    for entity in roster:
        for name in [entity["name"], *entity.get("aliases", [])]:
            if len(name) > 2 and re.search(rf"\b{re.escape(name.lower())}\b", q):
                candidates.append((len(name), entity["name"]))
    return max(candidates)[1] if candidates else None


def _find_predicate(question: str) -> str | None:
    q = question.lower()
    for predicate, keywords in _PREDICATE_KEYWORDS:
        if any(kw in q for kw in keywords):
            return predicate
    return None


def heuristic_classify(
    question: str, roster: list[dict], now: datetime | None = None
) -> Classification:
    q = question.lower()
    if any(m in q for m in _TEMPORAL_MARKERS):
        question_type = "temporal"
    elif any(m in q for m in _UPDATE_MARKERS):
        question_type = "knowledge_update"
    else:
        question_type = "lookup"
    as_of = None
    if "last week" in q:
        now = now or datetime.now(timezone.utc)
        as_of = (now - timedelta(days=7)).date().isoformat()
    return Classification(
        subject=_find_subject(question, roster),
        predicate=_find_predicate(question),
        question_type=question_type,
        as_of=as_of,
    )


def classify(
    question: str,
    roster: list[dict],
    llm_fn=None,
    now: datetime | None = None,
) -> Classification:
    """LLM classification with deterministic fallback.

    `llm_fn` takes a question string and returns a dict with any of
    {subject, predicate, question_type, as_of}; injectable for tests. When it
    is None — or its output is unusable — the heuristic decides everything.
    """
    fallback = heuristic_classify(question, roster, now)
    if llm_fn is None:
        return fallback
    try:
        raw = llm_fn(question)
        if not isinstance(raw, dict):
            raise ValueError("classifier returned non-dict")
        subject = raw.get("subject")
        predicate = raw.get("predicate")
        question_type = raw.get("question_type")
        return Classification(
            subject=(canonicalize_entity(subject, roster)
                     if isinstance(subject, str) and subject.strip()
                     else fallback.subject),
            predicate=(predicate if predicate in PREDICATES else fallback.predicate),
            question_type=(question_type if question_type in QUESTION_TYPES
                           else fallback.question_type),
            as_of=(str(raw["as_of"])[:10] if raw.get("as_of") else fallback.as_of),
        )
    except Exception:
        return fallback


def decide_route(question_type: str, probe: ProbeResult) -> str:
    if probe.predicate is None:
        # The question maps to no tracked predicate; coverage counts claims
        # about *other* facts, so any answer would be off-topic guesswork.
        return ROUTE_ABSTAIN
    if probe.coverage == 0:
        return ROUTE_ABSTAIN
    has_conflict = probe.conflicts > 0 or probe.distinct_active_values > 1
    if question_type == "lookup" and not has_conflict and probe.chain_depth <= 1:
        return ROUTE_FAST
    return ROUTE_DEEP
