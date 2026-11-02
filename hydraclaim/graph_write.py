"""Central HydraDB graph-write operations.

This module owns query text, graph identifiers, write order, and idempotency.
Reconciliation remains responsible for deciding which writes are needed.
"""

from __future__ import annotations

import math
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from hydraclaim.claims import (
    PREDICATES,
    QUALIFIED_PREDICATES,
    SOURCE_KINDS,
    claim_qualifier,
    requires_qualifier,
    validate_qualified_evidence,
    validate_scenario,
)
from hydraclaim.claim_read import ClaimReadLimitError, DEFAULT_CLAIM_READ_LIMIT
from hydraclaim.cypher import to_cypher_literal as lit
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.model import (
    claim_props,
    entity_key,
    entity_props,
    evidence_props,
    graph_id,
    source_key,
    source_props,
)
from hydraclaim.source_events import SourceEventStore


_VALID_STATUSES = frozenset({"active", "superseded", "disputed"})
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_MAX_LEGACY_QUALIFICATION_REPAIR_CLAIMS = 6
_CLAIM_FIELDS = frozenset(
    {
        "id",
        "key",
        "subject",
        "predicate",
        "qualifier",
        "value",
        "valid_from",
        "valid_to",
        "status",
        "confidence",
        "quote",
        "explicitness",
        "source_kind",
        "author",
        "session_id",
        "msg_id",
        "type",
        "supersedes",
        "contradicts_with",
    }
)
_ENTITY_FIELDS = frozenset({"name", "type", "aliases"})


def _props(props: dict) -> str:
    return "{" + ", ".join(f"{key}: {lit(value)}" for key, value in props.items()) + "}"


def _node_exists(db: Any, label: str, key: str) -> bool:
    row = db.query_one(
        f"MATCH (n:{label} {{id: {graph_id(key)}}}) RETURN count(*) AS c"
    )
    return bool(row and row.get("c", 0) > 0)


def _node_exists_id(db: Any, label: str, node_id: int) -> bool:
    row = db.query_one(f"MATCH (n:{label} {{id: {int(node_id)}}}) RETURN count(*) AS c")
    return bool(row and row.get("c", 0) > 0)


def _edge_exists(
    db: Any,
    label_a: str,
    id_a: int,
    rel: str,
    label_b: str,
    id_b: int,
) -> bool:
    row = db.query_one(
        f"MATCH (a:{label_a} {{id: {id_a}}})-[:{rel}]->"
        f"(b:{label_b} {{id: {id_b}}}) RETURN count(*) AS c"
    )
    return bool(row and row.get("c", 0) > 0)


def _write_claim(
    db: Any,
    claim_id: str,
    claim: dict,
    entity_endpoint: str,
    recorded_at: str,
) -> bool:
    """Ensure one claim, its provenance, and its entity attachment exist."""
    claim_properties = _props(claim_props(claim, claim_id, recorded_at))
    evidence_properties = _props(evidence_props(claim, claim_id))
    evidence_id = evidence_props(claim, claim_id)["id"]
    source_properties = _props(source_props(claim["source_kind"], claim["author"]))
    source_id = source_props(claim["source_kind"], claim["author"])["id"]
    claim_id_int = graph_id(claim_id)
    claim_exists = _node_exists(db, "Claim", claim_id)
    created_claim = not claim_exists
    evidence_exists = _node_exists_id(db, "Evidence", evidence_id)
    supported_by_created = False

    if not claim_exists:
        if not evidence_exists:
            db.query(
                f"CREATE (c:Claim {claim_properties})-[:SUPPORTED_BY]->"
                f"(ev:Evidence {evidence_properties})"
            )
            evidence_exists = True
            supported_by_created = True
        else:
            db.query(
                f"CREATE (c:Claim {claim_properties})-[:SUPPORTED_BY]->"
                f"(ev:Evidence {{id: {evidence_id}}})"
            )
            supported_by_created = True
        claim_exists = True
    if not supported_by_created and not _edge_exists(
        db, "Claim", claim_id_int, "SUPPORTED_BY", "Evidence", evidence_id
    ):
        if not evidence_exists:
            db.query(
                f"CREATE (c:Claim {{id: {claim_id_int}}})-[:SUPPORTED_BY]->"
                f"(ev:Evidence {evidence_properties})"
            )
            evidence_exists = True
        else:
            db.query(
                f"CREATE (c:Claim {{id: {claim_id_int}}})-[:SUPPORTED_BY]->"
                f"(ev:Evidence {{id: {evidence_id}}})"
            )

    if not _node_exists_id(db, "Source", source_id):
        source_exists = False
    else:
        source_exists = True
    if not _edge_exists(db, "Evidence", evidence_id, "FROM", "Source", source_id):
        source_endpoint = (
            f"(s:Source {{id: {source_id}}})"
            if source_exists
            else f"(s:Source {source_properties})"
        )
        db.query(
            f"CREATE (ev:Evidence {{id: {evidence_id}}})-[:FROM]->{source_endpoint}"
        )

    entity_id = _entity_id_from_endpoint(entity_endpoint)
    entity_exists = _node_exists_id(db, "Entity", entity_id)
    if not _edge_exists(db, "Claim", claim_id_int, "ABOUT", "Entity", entity_id):
        entity_target = (
            f"(e:Entity {{id: {entity_id}}})"
            if entity_exists
            else f"(e:Entity {entity_endpoint})"
        )
        db.query(f"CREATE (c:Claim {{id: {claim_id_int}}})-[:ABOUT]->{entity_target}")
    return created_claim


def _entity_id_from_endpoint(endpoint: str) -> int:
    match = re.search(r"id:\s*(\d+)", endpoint)
    if match is None:
        raise GraphIntegrityError("invalid Entity endpoint")
    return int(match.group(1))


def _write_edge(db: Any, rel: str, a_key: str, b_key: str, props: dict) -> bool:
    """Create a directed claim edge when it does not already exist."""
    a_id, b_id = graph_id(a_key), graph_id(b_key)
    if _edge_exists(db, "Claim", a_id, rel, "Claim", b_id):
        return False
    prop_str = f" {_props(props)}" if props else ""
    db.query(
        f"CREATE (a:Claim {{id: {a_id}}})-[:{rel}{prop_str}]->(b:Claim {{id: {b_id}}})"
    )
    return True


def _require_dict(value: object, name: str, errors: list[str]) -> dict:
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    return value


def _validate_non_empty_string(value: object, name: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{name} must be a string")
    elif not value.strip():
        errors.append(f"{name} must be a non-empty string")


def _validate_date(
    value: object, name: str, errors: list[str], *, allow_empty: bool = False
) -> None:
    if allow_empty and value == "":
        return
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        errors.append(f"{name} must use YYYY-MM-DD syntax")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{name} must be a valid calendar date")


def _validate_entity(entity: object, name: str, errors: list[str]) -> None:
    value = _require_dict(entity, name, errors)
    for field in sorted(set(value) - _ENTITY_FIELDS):
        errors.append(f"{name}.{field} is an unsupported property")
    _validate_non_empty_string(value.get("name"), f"{name}.name", errors)
    if "type" in value:
        _validate_non_empty_string(value["type"], f"{name}.type", errors)
    aliases = value.get("aliases", [])
    if not isinstance(aliases, list) or any(
        not isinstance(alias, str) for alias in aliases
    ):
        errors.append(f"{name}.aliases must be a list of strings")


def _validate_claim(claim: object, name: str, errors: list[str]) -> None:
    value = _require_dict(claim, name, errors)
    for field in sorted(set(value) - _CLAIM_FIELDS):
        errors.append(f"{name}.{field} is an unsupported property")
    required = {
        "subject",
        "predicate",
        "value",
        "valid_from",
        "quote",
        "author",
        "source_kind",
        "confidence",
        "explicitness",
    }
    missing = sorted(required - value.keys())
    if missing:
        errors.append(f"{name} missing keys: {missing}")
    if value.get("predicate") not in PREDICATES:
        errors.append(f"{name} has unknown predicate: {value.get('predicate')!r}")
    elif "predicate" in value:
        try:
            canonical = claim_qualifier(value["predicate"], value.get("qualifier"))
        except ValueError as exc:
            errors.append(f"{name}.qualifier is invalid: {exc}")
        else:
            supplied = value.get("qualifier")
            if canonical is not None and supplied != canonical:
                errors.append(f"{name}.qualifier must use canonical form {canonical!r}")
    if value.get("source_kind") not in SOURCE_KINDS:
        errors.append(f"{name} has unknown source_kind: {value.get('source_kind')!r}")
    for field in ("subject", "value", "quote", "author"):
        if field in value:
            _validate_non_empty_string(value[field], f"{name}.{field}", errors)
    if "valid_from" in value:
        _validate_date(value["valid_from"], f"{name}.valid_from", errors)
    if value.get("predicate") == "deadline" and isinstance(value.get("value"), str):
        _validate_date(value["value"], f"{name}.value", errors)
    if "type" in value:
        _validate_non_empty_string(value["type"], f"{name}.type", errors)
    if "status" in value and not isinstance(value["status"], str):
        errors.append(f"{name}.status must be a string")
    elif "status" in value and value["status"] not in _VALID_STATUSES:
        errors.append(f"{name}.status is invalid: {value['status']!r}")
    if "valid_to" in value and value["valid_to"] is not None:
        _validate_date(value["valid_to"], f"{name}.valid_to", errors, allow_empty=True)
    for field in ("session_id", "msg_id"):
        if field in value and not isinstance(value[field], str):
            errors.append(f"{name}.{field} must be a string")
    if "supersedes" in value and value["supersedes"] not in (None, ""):
        if not isinstance(value["supersedes"], str):
            errors.append(f"{name}.supersedes must be a claim id string or null")
    if "contradicts_with" in value:
        refs = value["contradicts_with"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            errors.append(f"{name}.contradicts_with must be a list of claim ids")
    for field in ("explicitness", "confidence"):
        field_value = value.get(field)
        if not isinstance(field_value, (int, float)) or isinstance(field_value, bool):
            errors.append(f"{name}.{field} must be numeric")
        elif not math.isfinite(float(field_value)) or not 0.0 <= field_value <= 1.0:
            errors.append(f"{name}.{field} out of range: {field_value}")
    if (
        value.get("predicate") in PREDICATES
        and isinstance(value.get("subject"), str)
        and isinstance(value.get("quote"), str)
        and isinstance(value.get("qualifier"), str)
    ):
        try:
            validate_qualified_evidence([value])
        except ValueError as exc:
            errors.append(f"{name} has invalid qualifier evidence: {exc}")


def _validate_qualified_claim_batch(
    claims: object, name: str, errors: list[str]
) -> None:
    if not isinstance(claims, list):
        return
    qualified = [
        claim
        for claim in claims
        if isinstance(claim, dict)
        and claim.get("predicate") in QUALIFIED_PREDICATES
        and isinstance(claim.get("subject"), str)
        and isinstance(claim.get("quote"), str)
        and isinstance(claim.get("qualifier"), str)
    ]
    try:
        validate_qualified_evidence(qualified)
    except ValueError as exc:
        errors.append(f"{name} has ambiguous qualified evidence: {exc}")


def _validate_document(document: object) -> list[str]:
    errors: list[str] = []
    doc = _require_dict(document, "document", errors)
    scenario_id = doc.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        errors.append("scenario_id must be a non-empty string")

    entities = doc.get("entities")
    if not isinstance(entities, list):
        errors.append("entities must be a list")
    else:
        for index, entity in enumerate(entities):
            _validate_entity(entity, f"entities[{index}]", errors)

    ground_truth = _require_dict(doc.get("ground_truth"), "ground_truth", errors)
    claims = ground_truth.get("claims")
    claim_keys: set[str] = set()
    if not isinstance(claims, list):
        errors.append("ground_truth.claims must be a list")
    else:
        for index, claim in enumerate(claims):
            _validate_claim(claim, f"ground_truth.claims[{index}]", errors)
            if isinstance(claim, dict):
                key = claim.get("key")
                if not isinstance(key, str) or not key:
                    errors.append(
                        f"ground_truth.claims[{index}].key must be a non-empty string"
                    )
                elif key in claim_keys:
                    errors.append(f"duplicate claim key: {key}")
                claim_keys.add(key)
        _validate_qualified_claim_batch(claims, "ground_truth.claims", errors)
        if isinstance(claims, list):
            for index, claim in enumerate(claims):
                if not isinstance(claim, dict):
                    continue
                for field in ("supersedes", "contradicts_with"):
                    refs = claim.get(field)
                    if field == "supersedes":
                        refs = [] if refs in (None, "") else [refs]
                    elif refs is None:
                        refs = []
                    if not isinstance(refs, list) or any(
                        not isinstance(ref, str) for ref in refs
                    ):
                        errors.append(
                            f"ground_truth.claims[{index}].{field} must contain claim keys"
                        )
                    else:
                        for ref in refs:
                            if ref not in claim_keys:
                                errors.append(
                                    f"{claim.get('key', index)} references unknown claim {ref}"
                                )

    try:
        errors.extend(validate_scenario(doc))
    except (KeyError, TypeError) as exc:
        errors.append(f"invalid scenario structure: {exc}")
    return errors


def _validate_plan(plan: object, scenario_id: object, entities: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(scenario_id, str) or not scenario_id:
        errors.append("scenario_id must be a non-empty string")
    plan_value = _require_dict(plan, "plan", errors)
    for key in ("create", "supersede", "contradict", "warnings"):
        if not isinstance(plan_value.get(key), list):
            errors.append(f"plan.{key} must be a list")
    warnings = plan_value.get("warnings", [])
    if isinstance(warnings, list) and any(
        not isinstance(warning, str) for warning in warnings
    ):
        errors.append("plan.warnings must contain strings")
    duplicates = plan_value.get("duplicates")
    if (
        not isinstance(duplicates, int)
        or isinstance(duplicates, bool)
        or duplicates < 0
    ):
        errors.append("plan.duplicates must be a non-negative integer")

    create = plan_value.get("create", [])
    create_ids: set[str] = set()
    if isinstance(create, list):
        for index, claim in enumerate(create):
            _validate_claim(claim, f"plan.create[{index}]", errors)
            if isinstance(claim, dict):
                claim_id = claim.get("id")
                if not isinstance(claim_id, str) or not claim_id:
                    errors.append(f"plan.create[{index}].id must be a non-empty string")
                elif claim_id in create_ids:
                    errors.append(f"duplicate plan claim id: {claim_id}")
                create_ids.add(claim_id)
        _validate_qualified_claim_batch(create, "plan.create", errors)

    supersede = plan_value.get("supersede", [])
    if isinstance(supersede, list):
        for index, edge in enumerate(supersede):
            edge_value = _require_dict(edge, f"plan.supersede[{index}]", errors)
            for field in ("new_id", "old_id", "at"):
                if not isinstance(edge_value.get(field), str) or not edge_value.get(
                    field
                ):
                    errors.append(
                        f"plan.supersede[{index}].{field} must be a non-empty string"
                    )
            if isinstance(edge_value.get("at"), str):
                _validate_date(edge_value["at"], f"plan.supersede[{index}].at", errors)

    contradict = plan_value.get("contradict", [])
    if isinstance(contradict, list):
        for index, edge in enumerate(contradict):
            edge_value = _require_dict(edge, f"plan.contradict[{index}]", errors)
            for field in ("a_id", "b_id"):
                if not isinstance(edge_value.get(field), str) or not edge_value.get(
                    field
                ):
                    errors.append(
                        f"plan.contradict[{index}].{field} must be a non-empty string"
                    )

    if entities is not None:
        if not isinstance(entities, list):
            errors.append("entities must be a list")
        else:
            for index, entity in enumerate(entities):
                _validate_entity(entity, f"entities[{index}]", errors)
    return errors


def _validate_relation_endpoints(
    db: Any,
    plan: dict,
    proposed_metadata: dict[str, tuple[str, str, str | None]],
) -> dict[str, tuple[str, str, str | None]]:
    """Validate relation endpoints and replace drafts with stored semantics.

    A replay can contain a claim id that already exists in the graph.  The
    incoming draft is not authoritative for that id.  Read the stored claim
    scope before relation validation so a replay cannot attach a relation to
    a different subject or predicate.
    """
    create_ids = {claim["id"] for claim in plan["create"]}
    references = (
        [("CREATE", claim["id"]) for claim in plan["create"]]
        + [("SUPERSEDES", edge["new_id"]) for edge in plan["supersede"]]
        + [("SUPERSEDES", edge["old_id"]) for edge in plan["supersede"]]
        + [("CONTRADICTS", edge["a_id"]) for edge in plan["contradict"]]
        + [("CONTRADICTS", edge["b_id"]) for edge in plan["contradict"]]
    )

    missing: list[str] = []
    checked: dict[str, bool] = {}
    authoritative = dict(proposed_metadata)
    for relation, claim_id in references:
        exists = checked.get(claim_id)
        if exists is None:
            exists = _node_exists(db, "Claim", claim_id)
            checked[claim_id] = exists
        if not exists:
            if relation != "CREATE" and claim_id not in create_ids:
                missing.append(f"{relation} endpoint {claim_id!r}")
            continue
        stored_scope = _claim_metadata(db, claim_id)
        draft_scope = proposed_metadata.get(claim_id)
        if draft_scope is not None and draft_scope != stored_scope:
            raise GraphIntegrityError(
                f"existing Claim {claim_id!r} scope differs from the proposed "
                f"scope: stored {stored_scope!r}, proposed {draft_scope!r}"
            )
        authoritative[claim_id] = stored_scope
    if missing:
        raise GraphIntegrityError(
            "invalid write plan: unknown Claim endpoint(s): " + ", ".join(missing)
        )
    return authoritative


def _claim_metadata(db: Any, claim_id: str) -> tuple[str, str, str | None]:
    row = db.query_one(
        f"MATCH (c:Claim {{id: {graph_id(claim_id)}}}) "
        "RETURN c.subject AS subject, c.predicate AS predicate, "
        "c.qualifier AS qualifier LIMIT 1"
    )
    if not (
        isinstance(row, dict)
        and isinstance(row.get("subject"), str)
        and row["subject"].strip()
        and isinstance(row.get("predicate"), str)
    ):
        # Claims written before subject became a Claim property still carry
        # the authoritative subject on their ABOUT edge. The qualifier stays
        # on Claim and cannot be recovered from this relation.
        row = db.query_one(
            f"MATCH (c:Claim {{id: {graph_id(claim_id)}}})-[:ABOUT]->(e:Entity) "
            "RETURN e.name AS subject, c.predicate AS predicate, "
            "c.qualifier AS qualifier LIMIT 1"
        )
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("subject"), str)
        or not isinstance(row.get("predicate"), str)
    ):
        raise GraphIntegrityError(f"cannot verify claim scope for Claim {claim_id!r}")
    predicate = row["predicate"]
    qualifier_value = row.get("qualifier")
    try:
        qualifier = claim_qualifier(predicate, qualifier_value)
    except ValueError as exc:
        raise GraphIntegrityError(
            f"cannot verify qualifier scope for Claim {claim_id!r}: {exc}"
        ) from exc
    if qualifier is not None and qualifier != qualifier_value:
        raise GraphIntegrityError(f"Claim {claim_id!r} has a non-canonical qualifier")
    return row["subject"], predicate, qualifier


def _existing_supersession_edges(
    db: Any, starting_ids: set[int]
) -> set[tuple[int, int]]:
    """Read all reachable existing supersession edges before a write."""
    pending = list(sorted(starting_ids))
    seen: set[int] = set()
    edges: set[tuple[int, int]] = set()
    while pending:
        current_id = pending.pop(0)
        if current_id in seen:
            continue
        seen.add(current_id)
        rows = db.query(
            f"MATCH (a:Claim {{id: {current_id}}})-[r:SUPERSEDES]->(b:Claim) "
            "RETURN a.id AS new_id, b.id AS old_id, "
            "r.invalidated_by AS invalidated_by "
            f"LIMIT {DEFAULT_CLAIM_READ_LIMIT + 1}"
        )
        if not isinstance(rows, list):
            raise GraphIntegrityError("invalid supersession edge response")
        if len(rows) > DEFAULT_CLAIM_READ_LIMIT:
            raise ClaimReadLimitError(
                "supersession integrity read limit exceeded",
                limit=DEFAULT_CLAIM_READ_LIMIT,
            )
        for row in rows:
            if not isinstance(row, dict):
                raise GraphIntegrityError("invalid supersession edge row")
            try:
                edge = (int(row["new_id"]), int(row["old_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise GraphIntegrityError("invalid supersession edge row") from exc
            invalidated_by = row.get("invalidated_by")
            if invalidated_by is not None:
                if not isinstance(invalidated_by, str) or not invalidated_by.strip():
                    raise GraphIntegrityError(
                        "SUPERSEDES edge has malformed invalidated_by marker"
                    )
                continue
            if edge not in edges:
                edges.add(edge)
                pending.append(edge[1])
    return edges


def _validate_supersession_integrity(
    db: Any,
    edges: list[dict],
    claim_metadata: dict[str, tuple[str, str, str | None]],
) -> None:
    """Reject invalid scope or cyclic supersession edges before any write."""
    if not edges:
        return
    planned: set[tuple[int, int]] = set()
    relevant_ids: set[int] = set()
    for edge in edges:
        new_key, old_key = edge["new_id"], edge["old_id"]
        if new_key == old_key:
            raise GraphIntegrityError(
                f"self-supersession is not allowed for Claim {new_key!r}"
            )
        new_scope = claim_metadata.get(new_key) or _claim_metadata(db, new_key)
        old_scope = claim_metadata.get(old_key) or _claim_metadata(db, old_key)
        if new_scope != old_scope:
            raise GraphIntegrityError(
                "SUPERSEDES endpoints must have the same subject and predicate "
                "with the same qualifier: "
                f"{new_key!r} -> {old_key!r}"
            )
        new_id, old_id = graph_id(new_key), graph_id(old_key)
        planned.add((new_id, old_id))
        relevant_ids.update((new_id, old_id))

    existing = _existing_supersession_edges(db, relevant_ids)
    combined = existing | planned
    indegree: dict[int, int] = {}
    forward: dict[int, set[int]] = {}
    for new_id, old_id in combined:
        indegree.setdefault(new_id, 0)
        indegree[old_id] = indegree.get(old_id, 0) + 1
        forward.setdefault(new_id, set()).add(old_id)
    pending = [node for node, degree in indegree.items() if degree == 0]
    processed = 0
    while pending:
        node = pending.pop()
        processed += 1
        for child in sorted(forward.get(node, ())):
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    if processed != len(indegree):
        raise GraphIntegrityError("SUPERSEDES cycle detected before graph write")


def _validate_contradiction_integrity(
    db: Any,
    edges: list[dict],
    claim_metadata: dict[str, tuple[str, str, str | None]],
) -> None:
    """Require CONTRADICTS to join two claims in one fact slot.

    The domain invariant is exact: a contradiction has two distinct Claim
    nodes, and both nodes have the same subject, predicate, and qualifier. A
    relation between different claim slots is not a contradiction.
    """
    for edge in edges:
        a_key, b_key = edge["a_id"], edge["b_id"]
        if a_key == b_key:
            raise GraphIntegrityError(
                f"self-CONTRADICTS is not allowed for Claim {a_key!r}"
            )
        a_scope = claim_metadata.get(a_key) or _claim_metadata(db, a_key)
        b_scope = claim_metadata.get(b_key) or _claim_metadata(db, b_key)
        if a_scope != b_scope:
            raise GraphIntegrityError(
                "CONTRADICTS endpoints must have the same subject and predicate "
                "with the same qualifier: "
                f"{a_key!r} -> {b_key!r}"
            )


@dataclass(frozen=True)
class GraphEventReviewSource:
    source_event_key: str
    expected_quoted_from_count: int
    source_content_sha256: str


@dataclass(frozen=True)
class PublicGitReviewSource:
    commit: str
    path: str
    scenario_id: str
    claim_key: str
    channel: str
    event_content: str
    event_content_sha256: str
    source_file_bytes: bytes
    source_file_sha256: str
    expected_quoted_from_count: int


@dataclass(frozen=True)
class LegacyQualificationReview:
    review_identity: str
    expected_evidence_id: int
    expected_evidence_key: str
    expected_source_id: int
    expected_source_key: str
    rationale: str
    source: GraphEventReviewSource | PublicGitReviewSource


@dataclass(frozen=True)
class QualificationClaimRepair:
    key: str
    expected_subject: str
    expected_predicate: str
    expected_value: str
    expected_valid_from: str
    expected_valid_to: str
    expected_status: str
    expected_quote: str
    expected_source_kind: str
    expected_author: str
    expected_source_event_key: str | None
    qualifier: str
    restore_status: str | None = None
    restore_valid_to: str | None = None
    legacy_review: LegacyQualificationReview | None = None


@dataclass(frozen=True)
class QualificationEdgeRepair:
    new_key: str
    old_key: str
    expected_at: str


@dataclass(frozen=True)
class QualificationRepair:
    repair_id: str
    claims: tuple[QualificationClaimRepair, ...]
    invalidated_edges: tuple[QualificationEdgeRepair, ...] = ()


@dataclass(frozen=True)
class QualificationEdgeState:
    new_key: str
    old_key: str
    at: str
    invalidated_by: str | None


@dataclass(frozen=True)
class QualificationClaimState:
    key: str
    qualifier: str | None
    status: str
    valid_to: str
    qualified_by_repair: str | None


@dataclass(frozen=True)
class QualificationRepairReceipt:
    repair_id: str
    dry_run: bool
    claims_before: tuple[QualificationClaimState, ...]
    claims_planned: tuple[QualificationClaimState, ...]
    claims_after: tuple[QualificationClaimState, ...] | None
    edges_before: tuple[QualificationEdgeState, ...]
    edges_planned: tuple[QualificationEdgeState, ...]
    edges_after: tuple[QualificationEdgeState, ...] | None
    invalidated_edges: tuple[QualificationEdgeRepair, ...]
    legacy_reviews: tuple[LegacyQualificationReview, ...]
    effects: tuple[str, ...]
    planned_effects: tuple[str, ...]


class QualificationRepairApplyError(RuntimeError):
    def __init__(
        self,
        *,
        repair_id: str,
        effects: tuple[str, ...],
        uncertain_step: str | None,
        failed_step: str,
        detail: str,
        claims_before: tuple[QualificationClaimState, ...],
        claims_planned: tuple[QualificationClaimState, ...],
        claims_after: tuple[QualificationClaimState | None, ...],
        edges_before: tuple[QualificationEdgeState, ...],
        edges_planned: tuple[QualificationEdgeState, ...],
        edges_after: tuple[QualificationEdgeState | None, ...],
        readback_errors: tuple[str, ...],
        invalidated_edges: tuple[QualificationEdgeRepair, ...],
        legacy_reviews: tuple[LegacyQualificationReview, ...],
    ) -> None:
        super().__init__(
            f"qualification repair {repair_id!r} failed during {failed_step}; "
            f"confirmed effects={effects!r}; uncertain_step={uncertain_step!r}; "
            f"readback_errors={readback_errors!r}; error={detail}"
        )
        self.repair_id = repair_id
        self.effects = effects
        self.uncertain_step = uncertain_step
        self.failed_step = failed_step
        self.claims_before = claims_before
        self.claims_planned = claims_planned
        self.claims_after = claims_after
        self.edges_before = edges_before
        self.edges_planned = edges_planned
        self.edges_after = edges_after
        self.readback_errors = readback_errors
        self.invalidated_edges = invalidated_edges
        self.legacy_reviews = legacy_reviews


def _validate_repair_input(repair: QualificationRepair, dry_run: bool) -> None:
    if not isinstance(repair, QualificationRepair):
        raise TypeError("repair must be a QualificationRepair")
    if not isinstance(dry_run, bool):
        raise TypeError("dry_run must be a boolean")
    if not isinstance(repair.repair_id, str) or not repair.repair_id.strip():
        raise ValueError("repair_id must be a non-empty string")
    if not 1 <= len(repair.claims) <= _MAX_LEGACY_QUALIFICATION_REPAIR_CLAIMS:
        raise ValueError(
            "legacy qualification repair claims must contain "
            f"1..{_MAX_LEGACY_QUALIFICATION_REPAIR_CLAIMS} items"
        )
    if len(repair.invalidated_edges) > _MAX_LEGACY_QUALIFICATION_REPAIR_CLAIMS:
        raise ValueError(
            "legacy qualification repair edges must contain at most "
            f"{_MAX_LEGACY_QUALIFICATION_REPAIR_CLAIMS} items"
        )
    keys: set[str] = set()
    for claim in repair.claims:
        if not isinstance(claim, QualificationClaimRepair):
            raise TypeError("repair claims must be QualificationClaimRepair values")
        if claim.key in keys:
            raise ValueError(f"duplicate repair Claim key: {claim.key!r}")
        keys.add(claim.key)
        for field in (
            "key",
            "expected_subject",
            "expected_predicate",
            "expected_value",
            "expected_valid_from",
            "expected_status",
            "expected_quote",
            "expected_source_kind",
            "expected_author",
            "qualifier",
        ):
            value = getattr(claim, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"repair claim {field} must be non-empty")
        if not requires_qualifier(claim.expected_predicate):
            raise ValueError("qualification repair requires a qualified predicate")
        if claim.expected_source_event_key is not None and (
            not isinstance(claim.expected_source_event_key, str)
            or not claim.expected_source_event_key.strip()
        ):
            raise ValueError(
                "repair expected_source_event_key must be non-empty or None"
            )
        if claim.legacy_review is None and claim.expected_source_event_key is None:
            raise ValueError("literal repair requires an exact Source Event key")
        if claim.expected_status not in _VALID_STATUSES:
            raise ValueError("repair expected_status is invalid")
        _validate_repair_date(claim.expected_valid_from, "expected_valid_from")
        _validate_repair_date(
            claim.expected_valid_to, "expected_valid_to", allow_empty=True
        )
        if (claim.restore_status is None) != (claim.restore_valid_to is None):
            raise ValueError("restore_status and restore_valid_to must appear together")
        if claim.restore_status is not None:
            if claim.restore_status not in _VALID_STATUSES:
                raise ValueError("repair restore_status is invalid")
            _validate_repair_date(
                claim.restore_valid_to, "restore_valid_to", allow_empty=True
            )
        canonical = claim_qualifier(claim.expected_predicate, claim.qualifier)
        if canonical != claim.qualifier:
            raise ValueError("repair qualifier must use canonical form")
        if claim.legacy_review is None:
            try:
                validate_qualified_evidence(
                    [
                        {
                            "subject": claim.expected_subject,
                            "predicate": claim.expected_predicate,
                            "qualifier": claim.qualifier,
                            "quote": claim.expected_quote,
                        }
                    ]
                )
            except ValueError as exc:
                raise ValueError(
                    f"repair qualifier is not anchored in Claim evidence {claim.key!r}"
                ) from exc
        else:
            review = claim.legacy_review
            if not isinstance(review, LegacyQualificationReview):
                raise TypeError("legacy_review must be a LegacyQualificationReview")
            for field in (
                "review_identity",
                "expected_evidence_key",
                "expected_source_key",
                "rationale",
            ):
                value = getattr(review, field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"legacy review {field} must be non-empty")
            for field in ("expected_evidence_id", "expected_source_id"):
                value = getattr(review, field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(
                        f"legacy review {field} must be a non-negative integer"
                    )
            expected_evidence_key = f"{claim.key}:ev0"
            expected_source_key = source_key(
                claim.expected_source_kind, claim.expected_author
            )
            if (
                review.expected_evidence_key != expected_evidence_key
                or review.expected_evidence_id != graph_id(expected_evidence_key)
                or review.expected_source_key != expected_source_key
                or review.expected_source_id != graph_id(expected_source_key)
            ):
                raise ValueError(
                    f"legacy review evidence/source identity does not match Claim {claim.key!r}"
                )
            source = review.source
            if not isinstance(source, (GraphEventReviewSource, PublicGitReviewSource)):
                raise TypeError("legacy review source has an unsupported type")
            if source.expected_quoted_from_count != 0:
                raise ValueError("legacy review must pin quoted_from_count=0")
            if isinstance(source, GraphEventReviewSource):
                if (
                    not isinstance(source.source_event_key, str)
                    or not source.source_event_key.strip()
                    or source.source_event_key != claim.expected_source_event_key
                ):
                    raise ValueError(
                        "review Source Event key must match the exact Claim mapping"
                    )
                claim_suffix = claim.key.removeprefix(f"{source.source_event_key}:x")
                if not claim_suffix.isdecimal():
                    raise ValueError(
                        "graph Source Event review requires Claim key eventprefix:xN"
                    )
            elif isinstance(source, PublicGitReviewSource):
                if claim.expected_source_event_key is not None:
                    raise ValueError(
                        "public Git review cannot claim a graph Source Event key"
                    )
                for field in (
                    "path",
                    "scenario_id",
                    "claim_key",
                    "channel",
                    "event_content",
                ):
                    value = getattr(source, field)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"public Git review {field} must be non-empty")
                if (
                    not isinstance(source.source_file_bytes, bytes)
                    or not source.source_file_bytes
                ):
                    raise ValueError(
                        "public Git review source_file_bytes must be non-empty bytes"
                    )
                if not re.fullmatch(r"[0-9a-f]{40}", source.commit):
                    raise ValueError("public Git review requires a full commit hash")
                if not claim.key.startswith(
                    f"{source.scenario_id}:"
                ) or not claim.key.endswith(f":{source.claim_key}"):
                    raise ValueError(
                        "public Git scenario/claim key does not match Claim key"
                    )
                file_digest = hashlib.sha256(source.source_file_bytes).hexdigest()
                if file_digest != source.source_file_sha256:
                    raise ValueError(
                        "public Git source bytes do not match pinned file digest"
                    )
                try:
                    public_source_text = source.source_file_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError("public Git source bytes must use UTF-8") from exc
                event_digest = hashlib.sha256(
                    source.event_content.encode("utf-8")
                ).hexdigest()
                if event_digest != source.event_content_sha256:
                    raise ValueError(
                        "public Git event content does not match pinned digest"
                    )
                if source.channel not in public_source_text:
                    raise ValueError(
                        "public Git source bytes do not contain the event channel"
                    )
                if source.event_content not in public_source_text:
                    raise ValueError(
                        "public Git source bytes do not contain exact event content"
                    )
                if claim.expected_quote not in source.event_content:
                    raise ValueError(
                        "public Git event content does not contain exact Claim quote"
                    )
    edge_pairs: set[tuple[str, str]] = set()
    for edge in repair.invalidated_edges:
        if not isinstance(edge, QualificationEdgeRepair):
            raise TypeError("repair edges must be QualificationEdgeRepair values")
        pair = (edge.new_key, edge.old_key)
        if pair in edge_pairs:
            raise ValueError(f"duplicate invalidated edge: {pair!r}")
        edge_pairs.add(pair)
        if not all(isinstance(key, str) and key.strip() for key in pair):
            raise ValueError("invalidated edge endpoints must be non-empty keys")
        if edge.new_key == edge.old_key:
            raise ValueError("invalidated edge endpoints must differ")
        if edge.new_key not in keys or edge.old_key not in keys:
            raise ValueError(
                "invalidated edge endpoints must be included in repair claims"
            )
        _validate_repair_date(edge.expected_at, "edge expected_at")


def _validate_repair_date(
    value: object, field: str, *, allow_empty: bool = False
) -> None:
    errors: list[str] = []
    _validate_date(value, f"repair {field}", errors, allow_empty=allow_empty)
    if errors:
        raise ValueError("; ".join(errors))


def _read_repair_claim(db: Any, expected: QualificationClaimRepair) -> dict:
    claim_key = expected.key
    rows = db.query(
        f"MATCH (c:Claim {{id: {graph_id(claim_key)}}})"
        "-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(s:Source) "
        "RETURN c.key AS key, c.subject AS subject, c.predicate AS predicate, "
        "c.qualifier AS qualifier, c.value AS value, c.valid_from AS valid_from, "
        "c.valid_to AS valid_to, c.status AS status, "
        "c.qualified_by_repair AS qualified_by_repair, ev.quote AS quote, "
        "s.kind AS source_kind, s.author AS author, ev.id AS evidence_id, "
        "ev.key AS evidence_key, s.id AS source_id, s.key AS source_key "
        f"LIMIT {DEFAULT_CLAIM_READ_LIMIT + 1}"
    )
    if not isinstance(rows, list):
        raise GraphIntegrityError(
            f"invalid repair read response for Claim {claim_key!r}"
        )
    if len(rows) != 1:
        raise GraphIntegrityError(
            f"repair requires one Claim/Evidence/Source Event row for {claim_key!r}; "
            f"found {len(rows)}"
        )
    row = rows[0]
    if not isinstance(row, dict):
        raise GraphIntegrityError(f"invalid repair row for Claim {claim_key!r}")
    try:
        evidence_id = int(row["evidence_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GraphIntegrityError(
            f"invalid Evidence identity for Claim {claim_key!r}"
        ) from exc
    linked_events = db.query(
        f"MATCH (ev:Evidence {{id: {evidence_id}}})-[:QUOTED_FROM]->(event:SourceEvent) "
        "RETURN event.key AS source_event_key "
        f"LIMIT {DEFAULT_CLAIM_READ_LIMIT + 1}"
    )
    if not isinstance(linked_events, list):
        raise GraphIntegrityError(
            f"invalid QUOTED_FROM read response for Claim {claim_key!r}"
        )
    review = expected.legacy_review
    review_source = review.source if review is not None else None
    expected_link_count = (
        review_source.expected_quoted_from_count if review_source is not None else 1
    )
    if len(linked_events) != expected_link_count:
        raise GraphIntegrityError(
            f"repair expected quoted_from_count={expected_link_count} for Claim "
            f"{claim_key!r}, found {len(linked_events)}"
        )
    if expected_link_count:
        if len(linked_events) != 1 or not isinstance(linked_events[0], dict):
            raise GraphIntegrityError(
                f"repair requires one exact QUOTED_FROM row for Claim {claim_key!r}"
            )
        row.update(
            _read_repair_source_event_by_key(
                db, linked_events[0].get("source_event_key")
            )
        )
    elif isinstance(review_source, GraphEventReviewSource):
        row.update(_read_repair_source_event_by_key(db, review_source.source_event_key))
    elif isinstance(review_source, PublicGitReviewSource):
        row.update(
            {
                "source_event_key": None,
                "source_content": review_source.event_content,
                "event_source_kind": expected.expected_source_kind,
                "event_author": expected.expected_author,
            }
        )
    else:
        raise GraphIntegrityError(
            f"repair has no pinned source for Claim {claim_key!r}"
        )
    return row


def _read_repair_source_event_by_key(db: Any, event_key: object) -> dict:
    if not isinstance(event_key, str) or not event_key.strip():
        raise GraphIntegrityError("repair Source Event key must be a non-empty string")
    rows = db.query(
        f"MATCH (event:SourceEvent {{key: {lit(event_key)}}}) "
        "RETURN event.key AS source_event_key, event.content AS source_content, "
        "event.source_kind AS event_source_kind, event.author AS event_author "
        f"LIMIT {DEFAULT_CLAIM_READ_LIMIT + 1}"
    )
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise GraphIntegrityError(
            f"repair requires one exact Source Event for key {event_key!r}"
        )
    return rows[0]


def _validate_repair_claim(
    row: dict, expected: QualificationClaimRepair, repair_id: str
) -> QualificationClaimState:
    exact = {
        "key": expected.key,
        "subject": expected.expected_subject,
        "predicate": expected.expected_predicate,
        "value": expected.expected_value,
        "valid_from": expected.expected_valid_from,
        "quote": expected.expected_quote,
        "source_kind": expected.expected_source_kind,
        "author": expected.expected_author,
    }
    for field, value in exact.items():
        if row.get(field) != value:
            raise GraphIntegrityError(
                f"repair preflight mismatch for Claim {expected.key!r}: "
                f"{field} expected {value!r}, found {row.get(field)!r}"
            )
    if row.get("source_event_key") != expected.expected_source_event_key:
        raise GraphIntegrityError(
            f"repair preflight mismatch for Claim {expected.key!r}: source_event_key "
            f"expected {expected.expected_source_event_key!r}, "
            f"found {row.get('source_event_key')!r}"
        )
    if (
        row.get("event_source_kind") != expected.expected_source_kind
        or row.get("event_author") != expected.expected_author
    ):
        raise GraphIntegrityError(
            f"repair Source Event source does not match Claim {expected.key!r}"
        )
    review = expected.legacy_review
    if review is not None:
        if row.get("evidence_id") != review.expected_evidence_id:
            raise GraphIntegrityError(
                f"legacy review Evidence id mismatch for Claim {expected.key!r}"
            )
        if row.get("evidence_key") != review.expected_evidence_key:
            raise GraphIntegrityError(
                f"legacy review evidence key mismatch for Claim {expected.key!r}"
            )
        if row.get("source_id") != review.expected_source_id:
            raise GraphIntegrityError(
                f"legacy review Source id mismatch for Claim {expected.key!r}"
            )
        if row.get("source_key") != review.expected_source_key:
            raise GraphIntegrityError(
                f"legacy review source key mismatch for Claim {expected.key!r}"
            )
    if (
        not isinstance(row.get("source_content"), str)
        or expected.expected_quote not in row["source_content"]
    ):
        raise GraphIntegrityError(
            f"repair quote for Claim {expected.key!r} is not in its immutable Source Event"
        )
    if review is not None:
        source_digest = hashlib.sha256(
            row["source_content"].encode("utf-8")
        ).hexdigest()
        expected_source_digest = (
            review.source.source_content_sha256
            if isinstance(review.source, GraphEventReviewSource)
            else review.source.event_content_sha256
        )
        if source_digest != expected_source_digest:
            raise GraphIntegrityError(
                f"legacy review Source Event digest mismatch for Claim {expected.key!r}"
            )
    current_qualifier = row.get("qualifier")
    marker = row.get("qualified_by_repair")
    already_applied = marker == repair_id
    if marker not in (None, "", repair_id):
        raise GraphIntegrityError(
            f"Claim {expected.key!r} is marked by another repair {marker!r}"
        )
    if already_applied:
        if current_qualifier != expected.qualifier:
            raise GraphIntegrityError(
                f"Claim {expected.key!r} repair marker conflicts with its qualifier"
            )
        expected_status = expected.restore_status or expected.expected_status
        expected_valid_to = (
            expected.restore_valid_to
            if expected.restore_valid_to is not None
            else expected.expected_valid_to
        )
    else:
        if current_qualifier is not None:
            raise GraphIntegrityError(
                f"Claim {expected.key!r} already has qualifier {current_qualifier!r}"
            )
        expected_status = expected.expected_status
        expected_valid_to = expected.expected_valid_to
    if row.get("status") != expected_status or row.get("valid_to") != expected_valid_to:
        raise GraphIntegrityError(
            f"repair preflight mismatch for Claim {expected.key!r}: "
            f"status/valid_to expected {(expected_status, expected_valid_to)!r}, "
            f"found {(row.get('status'), row.get('valid_to'))!r}"
        )
    return QualificationClaimState(
        key=expected.key,
        qualifier=current_qualifier,
        status=row["status"],
        valid_to=row["valid_to"],
        qualified_by_repair=marker,
    )


def _read_repair_edge(db: Any, edge: QualificationEdgeRepair) -> dict:
    rows = db.query(
        f"MATCH (new:Claim {{id: {graph_id(edge.new_key)}}})"
        f"-[r:SUPERSEDES]->(old:Claim {{id: {graph_id(edge.old_key)}}}) "
        "RETURN r.at AS at, r.invalidated_by AS invalidated_by "
        f"LIMIT {DEFAULT_CLAIM_READ_LIMIT + 1}"
    )
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise GraphIntegrityError(
            f"repair requires one exact SUPERSEDES edge "
            f"{edge.new_key!r}->{edge.old_key!r}"
        )
    marker = rows[0].get("invalidated_by")
    if marker is not None and (not isinstance(marker, str) or not marker.strip()):
        raise GraphIntegrityError(
            f"SUPERSEDES edge {edge.new_key!r}->{edge.old_key!r} has malformed invalidated_by"
        )
    return rows[0]


class GraphWriter:
    """Write validated claim graphs through the HydraDB query seam."""

    def __init__(self, db: Any):
        self._db = db

    def repair_qualifications(
        self,
        repair: QualificationRepair,
        *,
        dry_run: bool = True,
        public_source_verifier=None,
    ) -> QualificationRepairReceipt:
        """Preflight and apply a bounded source-backed repair.

        The caller pauses the sole graph writer before a non-dry run. This
        operation preserves quotes and provenance links.
        """
        _validate_repair_input(repair, dry_run)
        public_claims = [
            claim
            for claim in repair.claims
            if claim.legacy_review is not None
            and isinstance(claim.legacy_review.source, PublicGitReviewSource)
        ]
        if public_claims and public_source_verifier is None:
            raise GraphIntegrityError(
                "PublicGit legacy repair requires a trusted source verifier"
            )
        for claim in public_claims:
            try:
                verified = public_source_verifier(claim, claim.legacy_review.source)
            except Exception as exc:
                raise GraphIntegrityError(
                    f"PublicGit verifier failed for Claim {claim.key!r}: {exc}"
                ) from exc
            if verified is not True:
                raise GraphIntegrityError(
                    f"PublicGit verifier rejected Claim {claim.key!r}"
                )
        before: list[QualificationClaimState] = []
        after: list[QualificationClaimState] = []
        edge_before: list[dict] = []
        effects: list[str] = []
        claim_by_key = {claim.key: claim for claim in repair.claims}
        legacy_reviews = tuple(
            claim.legacy_review
            for claim in repair.claims
            if claim.legacy_review is not None
        )

        for claim in repair.claims:
            row = _read_repair_claim(self._db, claim)
            state = _validate_repair_claim(row, claim, repair.repair_id)
            before.append(state)
            after.append(
                QualificationClaimState(
                    key=claim.key,
                    qualifier=claim.qualifier,
                    status=claim.restore_status or state.status,
                    valid_to=(
                        claim.restore_valid_to
                        if claim.restore_valid_to is not None
                        else state.valid_to
                    ),
                    qualified_by_repair=repair.repair_id,
                )
            )

        for edge in repair.invalidated_edges:
            new_claim = claim_by_key.get(edge.new_key)
            old_claim = claim_by_key.get(edge.old_key)
            if new_claim is None or old_claim is None:
                raise ValueError(
                    "invalidated edge endpoints must be included in the repair claims"
                )
            state = _read_repair_edge(self._db, edge)
            if state["at"] != edge.expected_at:
                raise GraphIntegrityError(
                    f"SUPERSEDES edge {edge.new_key!r}->{edge.old_key!r} has unexpected at value"
                )
            invalidated_by = state.get("invalidated_by")
            if invalidated_by not in (None, repair.repair_id):
                raise GraphIntegrityError(
                    f"SUPERSEDES edge {edge.new_key!r}->{edge.old_key!r} is invalidated by another repair"
                )
            edge_before.append(state)

        edge_states_before = tuple(
            QualificationEdgeState(
                new_key=edge.new_key,
                old_key=edge.old_key,
                at=state["at"],
                invalidated_by=state.get("invalidated_by"),
            )
            for edge, state in zip(repair.invalidated_edges, edge_before)
        )
        edge_states_after = tuple(
            QualificationEdgeState(
                new_key=edge.new_key,
                old_key=edge.old_key,
                at=edge.expected_at,
                invalidated_by=repair.repair_id,
            )
            for edge in repair.invalidated_edges
        )

        if dry_run:
            return QualificationRepairReceipt(
                repair_id=repair.repair_id,
                dry_run=True,
                claims_before=tuple(before),
                claims_planned=tuple(after),
                claims_after=None,
                edges_before=edge_states_before,
                edges_planned=edge_states_after,
                edges_after=None,
                invalidated_edges=repair.invalidated_edges,
                legacy_reviews=legacy_reviews,
                effects=(),
                planned_effects=tuple(
                    [f"qualify Claim {claim.key}" for claim in repair.claims]
                    + [
                        f"invalidate SUPERSEDES {edge.new_key}->{edge.old_key}"
                        for edge in repair.invalidated_edges
                    ]
                ),
            )

        failed_step = "preparing repair writes"
        claims_readback: list[QualificationClaimState | None] | None = None
        edges_readback: list[QualificationEdgeState | None] | None = None
        readback_errors: list[str] = []
        try:
            for claim, old_state, new_state in zip(repair.claims, before, after):
                if old_state == new_state:
                    continue
                failed_step = f"qualify Claim {claim.key}"
                assignments = [
                    f"c.qualifier = {lit(claim.qualifier)}",
                    f"c.qualified_by_repair = {lit(repair.repair_id)}",
                ]
                if claim.restore_status is not None:
                    assignments.append(f"c.status = {lit(claim.restore_status)}")
                    assignments.append(f"c.valid_to = {lit(claim.restore_valid_to)}")
                self._db.query(
                    f"MATCH (c:Claim {{id: {graph_id(claim.key)}}}) SET "
                    + ", ".join(assignments)
                )
                effects.append(f"qualified Claim {claim.key}")
            for edge, edge_state in zip(repair.invalidated_edges, edge_before):
                if edge_state.get("invalidated_by") == repair.repair_id:
                    continue
                failed_step = f"invalidate SUPERSEDES {edge.new_key}->{edge.old_key}"
                self._db.query(
                    f"MATCH (new:Claim {{id: {graph_id(edge.new_key)}}})"
                    f"-[r:SUPERSEDES]->"
                    f"(old:Claim {{id: {graph_id(edge.old_key)}}}) "
                    f"SET r.invalidated_by = {lit(repair.repair_id)}"
                )
                effects.append(f"invalidated SUPERSEDES {edge.new_key}->{edge.old_key}")
            claims_readback = []
            edges_readback = []
            for claim in repair.claims:
                observed = None
                try:
                    row = _read_repair_claim(self._db, claim)
                    observed = QualificationClaimState(
                        key=row.get("key"),
                        qualifier=row.get("qualifier"),
                        status=row.get("status"),
                        valid_to=row.get("valid_to"),
                        qualified_by_repair=row.get("qualified_by_repair"),
                    )
                    expected = next(state for state in after if state.key == claim.key)
                    if observed != expected:
                        raise GraphIntegrityError(
                            f"Claim {claim.key!r} readback differs from planned state"
                        )
                    _validate_repair_claim(row, claim, repair.repair_id)
                except Exception as exc:
                    readback_errors.append(f"Claim {claim.key}: {exc}")
                claims_readback.append(observed)
            for edge in repair.invalidated_edges:
                try:
                    row = _read_repair_edge(self._db, edge)
                    if (
                        row.get("at") != edge.expected_at
                        or row.get("invalidated_by") != repair.repair_id
                    ):
                        raise GraphIntegrityError(
                            "readback does not match planned edge"
                        )
                    edges_readback.append(
                        QualificationEdgeState(
                            edge.new_key,
                            edge.old_key,
                            row["at"],
                            row.get("invalidated_by"),
                        )
                    )
                except Exception as exc:
                    edges_readback.append(None)
                    readback_errors.append(
                        f"SUPERSEDES {edge.new_key}->{edge.old_key}: {exc}"
                    )
            if readback_errors:
                failed_step = "verifying repair readback"
                raise GraphIntegrityError("; ".join(readback_errors))
        except Exception as exc:
            read_claims = claims_readback
            read_edges = edges_readback
            if read_claims is None:
                read_claims = []
                for claim in repair.claims:
                    try:
                        row = _read_repair_claim(self._db, claim)
                        read_claims.append(
                            QualificationClaimState(
                                key=row.get("key"),
                                qualifier=row.get("qualifier"),
                                status=row.get("status"),
                                valid_to=row.get("valid_to"),
                                qualified_by_repair=row.get("qualified_by_repair"),
                            )
                        )
                    except Exception as read_exc:
                        read_claims.append(None)
                        readback_errors.append(f"Claim {claim.key}: {read_exc}")
            if read_edges is None:
                read_edges = []
                for edge in repair.invalidated_edges:
                    try:
                        row = _read_repair_edge(self._db, edge)
                        read_edges.append(
                            QualificationEdgeState(
                                edge.new_key,
                                edge.old_key,
                                row.get("at"),
                                row.get("invalidated_by"),
                            )
                        )
                    except Exception as read_exc:
                        read_edges.append(None)
                        readback_errors.append(
                            f"SUPERSEDES {edge.new_key}->{edge.old_key}: {read_exc}"
                        )
            errors = tuple(readback_errors)
            confirmed = tuple(
                [
                    f"qualified Claim {claim.key}"
                    for claim, old, observed in zip(repair.claims, before, read_claims)
                    if observed is not None and observed != old
                ]
                + [
                    f"invalidated SUPERSEDES {edge.new_key}->{edge.old_key}"
                    for edge, old, observed in zip(
                        repair.invalidated_edges, edge_states_before, read_edges
                    )
                    if observed is not None and observed != old
                ]
            )
            raise QualificationRepairApplyError(
                repair_id=repair.repair_id,
                effects=confirmed,
                uncertain_step=failed_step
                if failed_step != "verifying repair readback"
                else None,
                failed_step=failed_step,
                detail=str(exc),
                claims_before=tuple(before),
                claims_planned=tuple(after),
                claims_after=tuple(read_claims),
                edges_before=edge_states_before,
                edges_planned=edge_states_after,
                edges_after=tuple(read_edges),
                readback_errors=errors,
                invalidated_edges=repair.invalidated_edges,
                legacy_reviews=legacy_reviews,
            ) from exc

        return QualificationRepairReceipt(
            repair_id=repair.repair_id,
            dry_run=False,
            claims_before=tuple(before),
            claims_planned=tuple(after),
            claims_after=tuple(claims_readback),
            edges_before=edge_states_before,
            edges_planned=edge_states_after,
            edges_after=tuple(edges_readback),
            invalidated_edges=repair.invalidated_edges,
            legacy_reviews=legacy_reviews,
            effects=tuple(effects),
            planned_effects=tuple(effects),
        )

    def ingest_document(self, document: dict) -> dict:
        errors = _validate_document(document)
        if errors:
            scenario_id = (
                document.get("scenario_id") if isinstance(document, dict) else None
            )
            raise ValueError(f"invalid scenario {scenario_id!r}: {errors}")

        scenario_id = document["scenario_id"]
        claims = document["ground_truth"]["claims"]
        recorded_at = datetime.now(timezone.utc).isoformat()
        stats = {
            "scenario": scenario_id,
            "claims": 0,
            "supersedes_edges": 0,
            "contradicts_edges": 0,
            "skipped_existing": 0,
            "warnings": [],
        }

        claimed_subjects = {claim["subject"] for claim in claims}
        entity_endpoint: dict[str, str] = {}
        for entity in document["entities"]:
            if entity["name"] not in claimed_subjects:
                stats["warnings"].append(
                    f"entity {entity['name']!r} has no claims; not created "
                    "(nodes are created paired with their first claim)"
                )
            else:
                entity_endpoint[entity["name"]] = _props(
                    entity_props(
                        scenario_id,
                        entity["name"],
                        entity.get("type", "unknown"),
                        entity.get("aliases", []),
                    )
                )

        supersede_edges = [
            {
                "new_id": f"{scenario_id}:{claim['key']}",
                "old_id": f"{scenario_id}:{claim['supersedes']}",
            }
            for claim in claims
            if claim.get("supersedes")
        ]
        claim_metadata = {
            f"{scenario_id}:{claim['key']}": (
                claim["subject"],
                claim["predicate"],
                claim_qualifier(claim["predicate"], claim.get("qualifier")),
            )
            for claim in claims
        }
        _validate_supersession_integrity(self._db, supersede_edges, claim_metadata)
        contradict_edges = [
            {
                "a_id": claim_id,
                "b_id": f"{scenario_id}:{other}",
            }
            for claim in claims
            for claim_id in [f"{scenario_id}:{claim['key']}"]
            for other in claim.get("contradicts_with", [])
        ]
        endpoint_plan = {
            "create": [{"id": f"{scenario_id}:{claim['key']}"} for claim in claims],
            "supersede": supersede_edges,
            "contradict": contradict_edges,
        }
        claim_metadata = _validate_relation_endpoints(
            self._db, endpoint_plan, claim_metadata
        )
        _validate_contradiction_integrity(self._db, contradict_edges, claim_metadata)

        for claim in claims:
            claim_id = f"{scenario_id}:{claim['key']}"
            subject = claim["subject"]
            source_event = {
                "source_kind": claim["source_kind"],
                "author": claim["author"],
                "occurred_at": claim["valid_from"],
                "content": claim["quote"],
            }
            if claim.get("msg_id"):
                source_event["source_id"] = claim["msg_id"]
            capture = SourceEventStore(self._db).capture(
                source_event, ingestion_kind="ORACLE"
            )
            endpoint = entity_endpoint.get(subject) or _props(
                entity_props(scenario_id, subject)
            )
            created = _write_claim(self._db, claim_id, claim, endpoint, recorded_at)
            evidence_id = evidence_props(claim, claim_id)["id"]
            source_event_id = graph_id(capture["event_key"])
            if not _edge_exists(
                self._db,
                "Evidence",
                evidence_id,
                "QUOTED_FROM",
                "SourceEvent",
                source_event_id,
            ):
                self._db.query(
                    f"CREATE (evidence:Evidence {{id: {evidence_id}}})"
                    f"-[:QUOTED_FROM]->(event:SourceEvent {{id: {source_event_id}}})"
                )
            if not created:
                stats["skipped_existing"] += 1
            entity_endpoint[subject] = (
                f"{{id: {graph_id(entity_key(scenario_id, subject))}}}"
            )
            if created:
                stats["claims"] += 1

        for claim in claims:
            claim_id = f"{scenario_id}:{claim['key']}"
            if claim.get("supersedes") and _write_edge(
                self._db,
                "SUPERSEDES",
                claim_id,
                f"{scenario_id}:{claim['supersedes']}",
                {"at": claim["valid_from"]},
            ):
                stats["supersedes_edges"] += 1
            for other in claim.get("contradicts_with", []):
                pair = sorted([claim_id, f"{scenario_id}:{other}"])
                if _write_edge(
                    self._db,
                    "CONTRADICTS",
                    pair[0],
                    pair[1],
                    {"resolved": False, "detected_at": recorded_at},
                ):
                    stats["contradicts_edges"] += 1
        return stats

    def apply_plan(
        self,
        plan: dict,
        scenario_id: str,
        entities: list[dict] | None = None,
        *,
        extraction_key: str | None = None,
        source_event_keys: dict[str, str] | None = None,
    ) -> dict:
        errors = _validate_plan(plan, scenario_id, entities)
        if errors:
            raise ValueError(f"invalid write plan for {scenario_id!r}: {errors}")
        claim_metadata = {
            draft["id"]: (
                draft["subject"],
                draft["predicate"],
                claim_qualifier(draft["predicate"], draft.get("qualifier")),
            )
            for draft in plan["create"]
        }
        claim_metadata = _validate_relation_endpoints(self._db, plan, claim_metadata)
        _validate_supersession_integrity(self._db, plan["supersede"], claim_metadata)
        _validate_contradiction_integrity(self._db, plan["contradict"], claim_metadata)

        recorded_at = datetime.now(timezone.utc).isoformat()
        stats = {
            "scenario": scenario_id,
            "created": 0,
            "entities_created": 0,
            "superseded": len(plan["supersede"]),
            "contradicted": len(plan["contradict"]),
            "duplicates": plan["duplicates"],
            "warnings": plan["warnings"],
        }
        entity_lookup = {entity["name"].strip(): entity for entity in (entities or [])}
        entity_endpoint: dict[str, str] = {}

        for draft in plan["create"]:
            claim_id = draft["id"]
            subject = draft["subject"]
            if subject in entity_endpoint:
                endpoint = entity_endpoint[subject]
            else:
                entity_key_value = entity_key(scenario_id, subject)
                if _node_exists(self._db, "Entity", entity_key_value):
                    endpoint = f"{{id: {graph_id(entity_key_value)}}}"
                else:
                    roster_entity = entity_lookup.get(subject.strip())
                    endpoint = _props(
                        entity_props(
                            scenario_id,
                            subject,
                            draft.get(
                                "type",
                                roster_entity.get("type", "unknown")
                                if roster_entity
                                else "unknown",
                            ),
                            roster_entity.get("aliases", []) if roster_entity else [],
                        )
                    )
                    stats["entities_created"] += 1
                entity_endpoint[subject] = endpoint
            if _write_claim(self._db, claim_id, draft, endpoint, recorded_at):
                stats["created"] += 1
            if extraction_key:
                self._db.query(
                    f"CREATE (claim:Claim {{id: {graph_id(claim_id)}}})"
                    f"-[:PRODUCED_BY]->(extraction:Extraction "
                    f"{{id: {graph_id(extraction_key)}}})"
                )
            source_event_key = (source_event_keys or {}).get(
                str(draft.get("msg_id", ""))
            )
            if source_event_key:
                self._db.query(
                    f"CREATE (evidence:Evidence {{id: {evidence_props(draft, claim_id)['id']}}})"
                    f"-[:QUOTED_FROM]->(event:SourceEvent "
                    f"{{id: {graph_id(source_event_key)}}})"
                )
            entity_endpoint[subject] = (
                f"{{id: {graph_id(entity_key(scenario_id, subject))}}}"
            )

        for edge in plan["supersede"]:
            _write_edge(
                self._db,
                "SUPERSEDES",
                edge["new_id"],
                edge["old_id"],
                {"at": edge["at"]},
            )
            old_id = graph_id(edge["old_id"])
            self._db.query(
                f"MATCH (old:Claim {{id: {old_id}}}) "
                f"SET old.valid_to = {lit(edge['at'])}, old.status = 'superseded'"
            )

        for edge in plan["contradict"]:
            _write_edge(
                self._db,
                "CONTRADICTS",
                edge["a_id"],
                edge["b_id"],
                {"resolved": False, "detected_at": recorded_at},
            )
        return stats
