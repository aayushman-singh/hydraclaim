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


def _same_fact(claim: dict, subject: str, predicate: str, value: str) -> bool:
    return (
        normalize_value(claim["subject"]) == normalize_value(subject)
        and claim["predicate"] == predicate
        and normalize_value(claim["value"]) == normalize_value(value)
    )


def _relates(claim: dict, subject: str, predicate: str) -> bool:
    return (
        normalize_value(claim["subject"]) == normalize_value(subject)
        and claim["predicate"] == predicate
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
        enriched = {
            **draft,
            "id": cid,
            "subject": subject,
            "status": "active",
            "valid_to": None,
        }

        # Rule 1: explicit supersession.
        if draft.get("supersedes"):
            target = by_id.get(draft["supersedes"])
            if target is None:
                warnings.append(
                    f"{cid}: supersedes target {draft['supersedes']!r} is not an active "
                    "claim; ingested as a plain new claim"
                )
            else:
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
            and _same_fact(c, subject, draft["predicate"], draft["value"])
            for c in active
        ):
            duplicates += 1
            continue

        # Rules 3+4: same fact slot, different value.
        conflicts = [
            c
            for c in active
            if c["status"] == "active"
            and _relates(c, subject, draft["predicate"])
            and normalize_value(c["value"]) != normalize_value(draft["value"])
        ]
        for c in conflicts:
            if (
                c["source_kind"] == draft["source_kind"]
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
