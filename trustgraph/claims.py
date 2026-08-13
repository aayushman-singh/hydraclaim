"""Claim vocabulary and ground-truth validation.

The predicate vocabulary is deliberately small and closed: extraction quality
is the biggest accuracy risk, and a fixed predicate set is what makes
typed-coverage abstention (`no claim for (subject, predicate)` -> abstain)
well-defined. Extend only when a scenario genuinely needs it.

A claim spec (produced by scenarios, consumed by the generator and ingest):

    key              unique within the scenario            (str)
    subject          entity name the claim is ABOUT        (str)
    predicate        one of PREDICATES                     (str)
    value            object value                          (str)
    day              offset from the scenario base date    (int)
    quote            verbatim supporting text              (str)
    author           evidence author                       (str)
    source_kind      slack | linear | meeting              (str)
    explicitness     0..1, how directly the quote states it (float)
    confidence       extraction confidence                 (float)
    supersedes       key of the claim this one overwrites  (str, optional)
    contradicts_with keys of unresolved conflicting claims (list, optional)
"""

from __future__ import annotations

PREDICATES = frozenset(
    {
        "owned_by",
        "assigned_to",
        "status",
        "deadline",
        "decided",
        "depends_on",
        "blocks",
        "reports_to",
        "works_on",
        "located_in",
        "prefers",
        "budget",
    }
)

SOURCE_KINDS = frozenset({"slack", "linear", "meeting"})

QUESTION_TYPES = frozenset(
    {
        "lookup",           # single-fact current-state question
        "temporal",         # "what was true at / before T"
        "knowledge_update", # answer is the latest in a supersession chain
        "conflict",         # requires surfacing unresolved contradictions
        "multi_session",    # synthesizes facts from multiple sessions
        "abstention",       # answer is not in the history
    }
)

_REQUIRED_CLAIM_KEYS = {
    "key",
    "subject",
    "predicate",
    "value",
    "day",
    "quote",
    "author",
    "source_kind",
}


def validate_claim_spec(claim: dict) -> list[str]:
    errors = []
    missing = _REQUIRED_CLAIM_KEYS - claim.keys()
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if claim.get("predicate") not in PREDICATES:
        errors.append(f"unknown predicate: {claim.get('predicate')!r}")
    if claim.get("source_kind") not in SOURCE_KINDS:
        errors.append(f"unknown source_kind: {claim.get('source_kind')!r}")
    for field in ("explicitness", "confidence"):
        value = claim.get(field, 1.0)
        if not 0.0 <= value <= 1.0:
            errors.append(f"{field} out of range: {value}")
    return errors


def validate_scenario(spec: dict) -> list[str]:
    """Structural checks run by the generator before anything is written."""
    errors = []
    claim_keys: set[str] = set()
    session_days: dict[str, int] = {}

    for event in spec.get("events", []):
        sid = event["session"]
        if sid in session_days and session_days[sid] != event["day"]:
            errors.append(f"session {sid} appears on multiple days")
        session_days[sid] = event["day"]
        for claim in event.get("claims", []):
            if claim["key"] in claim_keys:
                errors.append(f"duplicate claim key: {claim['key']}")
            claim_keys.add(claim["key"])
            errors.extend(validate_claim_spec(claim))

    for event in spec.get("events", []):
        for claim in event.get("claims", []):
            for ref in [claim.get("supersedes"), *claim.get("contradicts_with", [])]:
                if ref and ref not in claim_keys:
                    errors.append(f"{claim['key']} references unknown claim {ref}")

    for qa in spec.get("qa", []):
        if qa["qtype"] not in QUESTION_TYPES:
            errors.append(f"unknown qtype: {qa['qtype']}")
        if qa["qtype"] == "abstention":
            if qa["gold_claim_keys"]:
                errors.append(f"abstention question has gold claims: {qa['question']!r}")
            if qa["answer"] != "ABSTAIN":
                errors.append(f"abstention question without ABSTAIN answer: {qa['question']!r}")
        else:
            for ref in qa["gold_claim_keys"]:
                if ref not in claim_keys:
                    errors.append(f"qa references unknown claim {ref}: {qa['question']!r}")
    return errors
