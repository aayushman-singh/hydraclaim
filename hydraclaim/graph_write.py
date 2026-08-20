"""Central HydraDB graph-write operations.

This module owns query text, graph identifiers, write order, and idempotency.
Reconciliation remains responsible for deciding which writes are needed.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any

from hydraclaim.claims import PREDICATES, SOURCE_KINDS, validate_scenario
from hydraclaim.claim_read import ClaimReadLimitError, DEFAULT_CLAIM_READ_LIMIT
from hydraclaim.cypher import to_cypher_literal as lit
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.model import (
    claim_props,
    entity_key,
    entity_props,
    evidence_props,
    graph_id,
    source_props,
)


_VALID_STATUSES = frozenset({"active", "superseded", "disputed"})
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_CLAIM_FIELDS = frozenset(
    {
        "id",
        "key",
        "subject",
        "predicate",
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
            db.query(f"CREATE (c:Claim {claim_properties})")
        claim_exists = True
    if not supported_by_created and not _edge_exists(
        db, "Claim", claim_id_int, "SUPPORTED_BY", "Evidence", evidence_id
    ):
        if not evidence_exists:
            db.query(f"CREATE (ev:Evidence {evidence_properties})")
            evidence_exists = True
        db.query(
            f"CREATE (c:Claim {{id: {claim_id_int}}})-[:SUPPORTED_BY]->"
            f"(ev:Evidence {{id: {evidence_id}}})"
        )

    if not _node_exists_id(db, "Source", source_id):
        db.query(f"CREATE (s:Source {source_properties})")
    if not _edge_exists(db, "Evidence", evidence_id, "FROM", "Source", source_id):
        db.query(
            f"CREATE (ev:Evidence {{id: {evidence_id}}})-[:FROM]->"
            f"(s:Source {{id: {source_id}}})"
        )

    entity_id = _entity_id_from_endpoint(entity_endpoint)
    if not _node_exists_id(db, "Entity", entity_id):
        db.query(f"CREATE (e:Entity {entity_endpoint})")
    if not _edge_exists(db, "Claim", claim_id_int, "ABOUT", "Entity", entity_id):
        db.query(
            f"CREATE (c:Claim {{id: {claim_id_int}}})-[:ABOUT]->"
            f"(e:Entity {{id: {entity_id}}})"
        )
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


def _validate_scalar(value: object, name: str, errors: list[str]) -> None:
    if not isinstance(value, (str, int, float, bool)) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        errors.append(f"{name} must be a scalar")


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
    proposed_metadata: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
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


def _claim_metadata(db: Any, claim_id: str) -> tuple[str, str]:
    row = db.query_one(
        f"MATCH (c:Claim {{id: {graph_id(claim_id)}}}) "
        "RETURN c.subject AS subject, c.predicate AS predicate LIMIT 1"
    )
    if (
        isinstance(row, dict)
        and isinstance(row.get("subject"), str)
        and row["subject"].strip()
        and isinstance(row.get("predicate"), str)
    ):
        return row["subject"], row["predicate"]

    # Claims written before subject became a Claim property still carry the
    # authoritative subject on their ABOUT edge.
    row = db.query_one(
        f"MATCH (c:Claim {{id: {graph_id(claim_id)}}})-[:ABOUT]->(e:Entity) "
        "RETURN e.name AS subject, c.predicate AS predicate LIMIT 1"
    )
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("subject"), str)
        or not isinstance(row.get("predicate"), str)
    ):
        raise GraphIntegrityError(f"cannot verify claim scope for Claim {claim_id!r}")
    return row["subject"], row["predicate"]


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
            f"MATCH (a:Claim {{id: {current_id}}})-[:SUPERSEDES]->(b:Claim) "
            "RETURN a.id AS new_id, b.id AS old_id "
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
            if edge not in edges:
                edges.add(edge)
                pending.append(edge[1])
    return edges


def _validate_supersession_integrity(
    db: Any,
    edges: list[dict],
    claim_metadata: dict[str, tuple[str, str]],
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
                "SUPERSEDES endpoints must have the same subject and predicate: "
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
    claim_metadata: dict[str, tuple[str, str]],
) -> None:
    """Require CONTRADICTS to join two claims in one fact slot.

    The domain invariant is exact: a contradiction has two distinct Claim
    nodes, and both nodes have the same subject and predicate.  A relation
    between different fact slots is not a contradiction.
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
                "CONTRADICTS endpoints must have the same subject and predicate: "
                f"{a_key!r} -> {b_key!r}"
            )


class GraphWriter:
    """Write validated claim graphs through the HydraDB query seam."""

    def __init__(self, db: Any):
        self._db = db

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
            endpoint = entity_endpoint.get(subject) or _props(
                entity_props(scenario_id, subject)
            )
            created = _write_claim(self._db, claim_id, claim, endpoint, recorded_at)
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
    ) -> dict:
        errors = _validate_plan(plan, scenario_id, entities)
        if errors:
            raise ValueError(f"invalid write plan for {scenario_id!r}: {errors}")
        claim_metadata = {
            draft["id"]: (draft["subject"], draft["predicate"])
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
