"""Trust scoring for conflicting active claims.

    score = 0.35 * authority(source_kind, predicate)
          + 0.20 * recency_decay(valid_from)
          + 0.20 * author_authority(author, source_kind, value)
          + 0.15 * explicitness
          + 0.10 * extraction_confidence

Two rules keep this defensible (see PLAN.md):
- Authority is per-(source-kind, predicate), not per source. Linear is
  authoritative for assigned_to/status, weak for deadline; meeting notes are
  authoritative for decided. KIND_DEFAULT applies when no override exists.
- Scoring only arbitrates TRUE conflicts: active claims with different values
  and no SUPERSEDES edge between them. Explicit supersession always wins
  before scoring (enforced in reconcile.py).

Pure functions; `now` is injectable for tests.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

AUTHORITY: dict[tuple[str, str], float] = {
    ("linear", "assigned_to"): 0.9,
    ("linear", "status"): 0.9,
    ("linear", "owned_by"): 0.7,
    ("linear", "deadline"): 0.4,
    ("meeting", "decided"): 0.9,
    ("meeting", "deadline"): 0.8,
    ("meeting", "owned_by"): 0.75,
    ("meeting", "budget"): 0.85,
}

KIND_DEFAULT: dict[str, float] = {
    "linear": 0.7,
    "meeting": 0.75,
    "slack": 0.5,
    "unknown": 0.4,
}

AUTHOR_BASE: dict[str, float] = {
    "linear": 0.9,    # system-of-record entries
    "meeting": 0.85,  # official notes
}

HALF_LIFE_DAYS = 30.0
RECENCY_FLOOR = 0.05


def _as_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def recency_decay(valid_from: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    days = max((now.date() - _as_date(valid_from)).days, 0)
    return max(0.5 ** (days / HALF_LIFE_DAYS), RECENCY_FLOOR)


def authority(source_kind: str, predicate: str) -> float:
    return AUTHORITY.get((source_kind, predicate), KIND_DEFAULT.get(source_kind, 0.4))


def author_authority(author: str, source_kind: str, value: str) -> float:
    base = AUTHOR_BASE.get(source_kind, 0.6)
    # Self-announcement bonus: "I'm taking over X" from Dario carries more
    # weight than the same sentence from a bystander.
    surname = author.split()[-1].lower() if author.split() else ""
    if len(surname) > 2 and surname in str(value).lower():
        base += 0.15
    return min(base, 1.0)


def score_claim(claim: dict, predicate: str, now: datetime | None = None) -> float:
    kind = claim.get("source_kind") or "unknown"
    return (
        0.35 * authority(kind, predicate)
        + 0.20 * recency_decay(claim["valid_from"], now)
        + 0.20 * author_authority(claim.get("author", ""), kind, claim.get("value", ""))
        + 0.15 * float(claim.get("explicitness", 1.0))
        + 0.10 * float(claim.get("extraction_confidence", claim.get("confidence", 0.5)))
    )


def rank_claims(
    claims: list[dict], predicate: str, now: datetime | None = None
) -> list[tuple[dict, float]]:
    """Active conflicting claims, highest score first."""
    scored = [(c, score_claim(c, predicate, now)) for c in claims]
    scored.sort(key=lambda pair: (pair[1], pair[0]["valid_from"]), reverse=True)
    return scored
