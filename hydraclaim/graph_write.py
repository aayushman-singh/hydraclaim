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
from hydraclaim.cypher import to_cypher_literal as lit
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
) -> None:
    """Create one claim, its evidence and source, and its entity attachment."""
    claim_properties = _props(claim_props(claim, claim_id, recorded_at))
    evidence_properties = _props(evidence_props(claim, claim_id))
    evidence_id = evidence_props(claim, claim_id)["id"]
    source_properties = _props(source_props(claim["source_kind"], claim["author"]))

    db.query(
        f"CREATE (c:Claim {claim_properties})-[:SUPPORTED_BY]->"
        f"(ev:Evidence {evidence_properties})"
    )
    db.query(
        f"CREATE (ev:Evidence {{id: {evidence_id}}})-[:FROM]->"
        f"(s:Source {source_properties})"
    )
    db.query(
        f"CREATE (c:Claim {{id: {graph_id(claim_id)}}})-[:ABOUT]->"
        f"(e:Entity {entity_endpoint})"
    )


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


def _validate_relation_endpoints(db: Any, plan: dict) -> None:
    create_ids = {claim["id"] for claim in plan["create"]}
    references = (
        [("SUPERSEDES", edge["new_id"]) for edge in plan["supersede"]]
        + [("SUPERSEDES", edge["old_id"]) for edge in plan["supersede"]]
        + [("CONTRADICTS", edge["a_id"]) for edge in plan["contradict"]]
        + [("CONTRADICTS", edge["b_id"]) for edge in plan["contradict"]]
    )

    missing: list[str] = []
    checked: dict[str, bool] = {}
    for relation, claim_id in references:
        if claim_id in create_ids:
            continue
        exists = checked.get(claim_id)
        if exists is None:
            exists = _node_exists(db, "Claim", claim_id)
            checked[claim_id] = exists
        if not exists:
            missing.append(f"{relation} endpoint {claim_id!r}")
    if missing:
        raise ValueError(
            "invalid write plan: unknown Claim endpoint(s): " + ", ".join(missing)
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

        for claim in claims:
            claim_id = f"{scenario_id}:{claim['key']}"
            if _node_exists(self._db, "Claim", claim_id):
                stats["skipped_existing"] += 1
                continue
            subject = claim["subject"]
            endpoint = entity_endpoint.get(subject) or _props(
                entity_props(scenario_id, subject)
            )
            _write_claim(self._db, claim_id, claim, endpoint, recorded_at)
            entity_endpoint[subject] = (
                f"{{id: {graph_id(entity_key(scenario_id, subject))}}}"
            )
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
        _validate_relation_endpoints(self._db, plan)

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
            if _node_exists(self._db, "Claim", claim_id):
                continue
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
            _write_claim(self._db, claim_id, draft, endpoint, recorded_at)
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
