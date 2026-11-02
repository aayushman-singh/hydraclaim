"""Deterministic reconciliation: claim drafts -> graph write plan.

The LLM extracts and links explicit overwrites; everything else about graph
consistency is decided here, by rules that can be unit-tested offline:

1. An explicit supersession must identify an active Claim in the same scope.
2. An exact subject, predicate, qualifier, and value duplicate is skipped.
3. A different decision or preference qualifier is independent.
4. A different value in a qualified scope remains a conflict unless it names
   an explicit supersession target.
5. For unqualified predicates, a later same-source value supersedes the old
   value. A different source kind creates an unresolved contradiction.

Value comparisons normalize case/whitespace. Draft ids supplied by the
caller (`id` key) are honored; missing ids are assigned `{id_prefix}:x{N}`.
"""

from __future__ import annotations

import re

from hydraclaim.claims import claim_qualifier, requires_qualifier
from hydraclaim.graph_write import GraphWriter


def normalize_value(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


_norm = normalize_value


def canonicalize_entity(name: str, roster: list[dict]) -> str:
    low = name.strip().lower()
    for entity in roster:
        if low == entity["name"].strip().lower():
            return entity["name"]
        if low in [a.strip().lower() for a in entity.get("aliases", [])]:
            return entity["name"]
    return name.strip()


def _same_fact(
    claim: dict, subject: str, predicate: str, value: str, qualifier: str | None
) -> bool:
    return (
        normalize_value(claim["subject"]) == normalize_value(subject)
        and claim["predicate"] == predicate
        and claim_qualifier(predicate, claim.get("qualifier")) == qualifier
        and normalize_value(claim["value"]) == normalize_value(value)
    )


def _relates(claim: dict, subject: str, predicate: str, qualifier: str | None) -> bool:
    return (
        normalize_value(claim["subject"]) == normalize_value(subject)
        and claim["predicate"] == predicate
        and claim_qualifier(predicate, claim.get("qualifier")) == qualifier
    )


def plan_writes(
    drafts: list[dict],
    active_claims: list[dict],
    roster: list[dict],
    id_prefix: str = "draft",
) -> dict:
    active = [
        dict(c, status=c.get("status", "active"), valid_to=c.get("valid_to"))
        for c in active_claims
    ]
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
        qualifier = claim_qualifier(draft["predicate"], draft.get("qualifier"))
        enriched = {
            **draft,
            "id": cid,
            "subject": subject,
            "status": "active",
            "valid_to": None,
        }

        if qualifier is not None:
            enriched["qualifier"] = qualifier

        # Rule 1: explicit supersession.
        if draft.get("supersedes"):
            target = by_id.get(draft["supersedes"])
            if target is None:
                if requires_qualifier(draft["predicate"]):
                    raise ValueError(
                        "qualified supersedes target is not an active claim"
                    )
                warnings.append(
                    f"{cid}: supersedes target {draft['supersedes']!r} is not an active "
                    "claim; ingested as a plain new claim"
                )
            else:
                if not _relates(target, subject, draft["predicate"], qualifier):
                    raise ValueError("supersedes target has a different claim scope")
                supersede.append(
                    {"new_id": cid, "old_id": target["id"], "at": draft["valid_from"]}
                )
                target["status"] = "superseded"
                target["valid_to"] = draft["valid_from"]
                create.append(enriched)
                active.append(dict(enriched))
                by_id[cid] = active[-1]
                continue

        # Rule 2: exact duplicate.
        if any(
            c["status"] == "active"
            and _same_fact(c, subject, draft["predicate"], draft["value"], qualifier)
            for c in active
        ):
            duplicates += 1
            continue

        # Rules 3+4: same fact slot, different value.
        conflicts = [
            c
            for c in active
            if c["status"] == "active"
            and _relates(c, subject, draft["predicate"], qualifier)
            and normalize_value(c["value"]) != normalize_value(draft["value"])
        ]
        for c in conflicts:
            if (
                not requires_qualifier(draft["predicate"])
                and c["source_kind"] == draft["source_kind"]
                and draft["valid_from"] >= c["valid_from"]
            ):
                # Rule 3: a source correcting itself.
                supersede.append(
                    {"new_id": cid, "old_id": c["id"], "at": draft["valid_from"]}
                )
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


def apply_plan(
    db, plan: dict, scenario_id: str, entities: list[dict] | None = None
) -> dict:
    """Apply a write plan through the central graph writer."""
    return GraphWriter(db).apply_plan(plan, scenario_id, entities)
