from __future__ import annotations

import re
from copy import deepcopy

import pytest

from hydraclaim import ingest, reconcile
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.graph_write import GraphWriter
from hydraclaim.model import graph_id


class RecordingDB:
    def __init__(self):
        self.queries = []
        self.writes = []

    def query(self, cypher):
        self.queries.append(cypher)
        if cypher.lstrip().startswith("CREATE") or " SET " in cypher:
            self.writes.append(cypher)
        return []

    def query_one(self, cypher):
        self.queries.append(cypher)
        return {"c": 0}


class IdempotentDB(RecordingDB):
    def __init__(self):
        super().__init__()
        self.nodes = {
            label: set() for label in ("Claim", "Evidence", "Source", "Entity")
        }
        self.claim_scopes = {}
        self.edges = set()

    def query(self, cypher):
        result = super().query(cypher)
        for label, node_id in re.findall(
            r":(Claim|Evidence|Source|Entity) \{id: (\d+)", cypher
        ):
            self.nodes[label].add(int(node_id))
        claim = re.search(
            r":Claim \{id: (?P<id>\d+), key: '[^']*', subject: '(?P<subject>[^']*)', "
            r"predicate: '(?P<predicate>[^']*)'",
            cypher,
        )
        if claim:
            self.claim_scopes[int(claim["id"])] = (
                claim["subject"],
                claim["predicate"],
            )
        for label_a, id_a, relation, label_b, id_b in re.findall(
            r"\((?:\w+):(?P<label_a>\w+) \{id: (?P<id_a>\d+)[^}]*\}\)-\[:(?P<relation>\w+)\]->"
            r"\((?:\w+):(?P<label_b>\w+) \{id: (?P<id_b>\d+)[^}]*\}\)",
            cypher,
        ):
            self.edges.add((label_a, int(id_a), relation, label_b, int(id_b)))
        return result

    def query_one(self, cypher):
        self.queries.append(cypher)
        if "RETURN c.subject AS subject" in cypher:
            claim_id = int(cypher.split("id: ", 1)[1].split("}", 1)[0])
            scope = self.claim_scopes.get(claim_id)
            return (
                {"subject": scope[0], "predicate": scope[1]}
                if scope is not None
                else None
            )
        edge = re.search(
            r"\((?P<label_a>\w+):(?P<type_a>\w+) \{id: (?P<id_a>\d+)\}\)-\[:(?P<relation>\w+)\]->"
            r"\((?P<label_b>\w+):(?P<type_b>\w+) \{id: (?P<id_b>\d+)\}\)",
            cypher,
        )
        if edge:
            values = edge.groupdict()
            return {
                "c": int(
                    (
                        values["type_a"],
                        int(values["id_a"]),
                        values["relation"],
                        values["type_b"],
                        int(values["id_b"]),
                    )
                    in self.edges
                )
            }
        node = re.search(r"\(n:(?P<label>\w+) \{id: (?P<id>\d+)\}", cypher)
        if node:
            values = node.groupdict()
            return {"c": int(int(values["id"]) in self.nodes[values["label"]])}
        return {"c": 0}


def _claim(key="c1", **overrides):
    claim = {
        "key": key,
        "subject": "project",
        "predicate": "status",
        "value": "active",
        "valid_from": "2026-05-01",
        "quote": "The project is active.",
        "author": "Asha Rao",
        "source_kind": "slack",
        "confidence": 0.9,
        "explicitness": 1.0,
        "session_id": "s1",
        "msg_id": "s1-m1",
        "supersedes": None,
        "contradicts_with": [],
    }
    claim.update(overrides)
    return claim


def _scenario():
    claim = _claim()
    return {
        "scenario_id": "scenario",
        "entities": [{"name": "project", "type": "project", "aliases": []}],
        "ground_truth": {"claims": [claim], "qa": []},
    }


def _plan():
    claim = _claim(id="scenario:c1")
    return {
        "create": [claim],
        "supersede": [],
        "contradict": [],
        "duplicates": 0,
        "warnings": [],
    }


def test_writer_creates_claims_before_claim_relations():
    db = RecordingDB()
    scenario = _scenario()
    scenario["ground_truth"]["claims"].append(
        _claim(
            key="c2",
            value="done",
            supersedes="c1",
        )
    )
    scenario["ground_truth"]["claims"].append(
        _claim(
            key="c3",
            value="blocked",
            contradicts_with=["c1"],
        )
    )

    GraphWriter(db).ingest_document(scenario)

    claim_indices = [i for i, query in enumerate(db.writes) if "SUPPORTED_BY" in query]
    relation_indices = [
        i
        for i, query in enumerate(db.writes)
        if ":SUPERSEDES" in query or ":CONTRADICTS" in query
    ]
    assert claim_indices
    assert relation_indices
    assert max(claim_indices) < min(relation_indices)


def test_apply_plan_does_not_duplicate_existing_claim_writes():
    db = IdempotentDB()
    plan = _plan()
    writer = GraphWriter(db)

    writer.apply_plan(plan, "scenario")
    first_write_count = len([q for q in db.writes if "SUPPORTED_BY" in q])
    writer.apply_plan(plan, "scenario")

    assert first_write_count == 1
    assert len([q for q in db.writes if "SUPPORTED_BY" in q]) == 1
    assert len([q for q in db.writes if "[:ABOUT]" in q]) == 1


def test_apply_plan_links_claim_and_evidence_to_processing_provenance():
    db = RecordingDB()

    GraphWriter(db).apply_plan(
        _plan(),
        "scenario",
        extraction_key="extraction-1",
        source_event_keys={"s1-m1": "source-event-1"},
    )

    writes = "\n".join(db.writes)
    assert "PRODUCED_BY" in writes
    assert "QUOTED_FROM" in writes


def test_apply_plan_rejects_unknown_relation_endpoint_before_writes():
    db = RecordingDB()
    plan = _plan()
    plan["supersede"] = [
        {"new_id": "scenario:c1", "old_id": "scenario:missing", "at": "2026-05-02"}
    ]

    with pytest.raises(ValueError, match="SUPERSEDES.*scenario:missing"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []
    assert db.queries


def test_apply_plan_rejects_invalid_model_property_before_writes():
    db = RecordingDB()
    plan = _plan()
    plan["create"][0]["session_id"] = {"not": "scalar"}

    with pytest.raises(ValueError, match="session_id"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.queries == []


def test_public_ingest_wrapper_delegates_to_graph_writer(monkeypatch):
    expected = {"claims": 1}

    class StubWriter:
        def __init__(self, db):
            self.db = db

        def ingest_document(self, document):
            return expected

    monkeypatch.setattr(ingest, "GraphWriter", StubWriter)
    assert ingest.ingest_document(object(), _scenario()) is expected


def test_public_reconcile_wrapper_delegates_to_graph_writer(monkeypatch):
    expected = {"created": 1}

    class StubWriter:
        def __init__(self, db):
            self.db = db

        def apply_plan(self, plan, scenario_id, entities):
            return expected

    monkeypatch.setattr(reconcile, "GraphWriter", StubWriter)
    assert reconcile.apply_plan(object(), _plan(), "scenario", []) is expected


def test_ingest_validates_before_first_query():
    db = RecordingDB()
    invalid = _scenario()
    invalid["ground_truth"]["claims"][0].pop("quote")

    with pytest.raises(ValueError, match="quote"):
        GraphWriter(db).ingest_document(invalid)

    assert db.queries == []


def test_apply_plan_validates_before_first_query():
    db = RecordingDB()
    invalid = _plan()
    invalid["create"][0].pop("quote")

    with pytest.raises(ValueError, match="quote"):
        GraphWriter(db).apply_plan(invalid, "scenario")

    assert db.queries == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valid_from", "2026-02-30"),
        ("valid_to", "not-a-date"),
        ("subject", ""),
        ("value", "  "),
        ("quote", ""),
        ("author", ""),
        ("status", "unknown"),
        ("confidence", 1.1),
        ("explicitness", -0.1),
        ("session_id", {"not": "scalar"}),
        ("unsupported", {"nested": "property"}),
    ],
)
def test_apply_plan_rejects_invalid_claim_properties_before_any_write(field, value):
    db = RecordingDB()
    plan = deepcopy(_plan())
    plan["create"][0][field] = value

    with pytest.raises(ValueError, match=field):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []
    assert db.queries == []


def test_ingest_rejects_invalid_claim_properties_before_any_write():
    db = RecordingDB()
    scenario = deepcopy(_scenario())
    scenario["ground_truth"]["claims"][0]["type"] = {"unsupported": True}

    with pytest.raises(ValueError, match="type"):
        GraphWriter(db).ingest_document(scenario)

    assert db.writes == []
    assert db.queries == []


def test_ingest_rejects_self_contradiction_before_any_write():
    db = RecordingDB()
    scenario = _scenario()
    scenario["ground_truth"]["claims"][0]["contradicts_with"] = ["c1"]

    with pytest.raises(GraphIntegrityError, match="self-CONTRADICTS"):
        GraphWriter(db).ingest_document(scenario)

    assert db.writes == []


@pytest.mark.parametrize(
    ("subject", "predicate"),
    [("person", "status"), ("project", "deadline")],
    ids=("cross-subject", "cross-predicate"),
)
def test_ingest_rejects_cross_slot_contradiction_before_any_write(subject, predicate):
    db = RecordingDB()
    scenario = _scenario()
    scenario["entities"].append({"name": "person", "type": "person", "aliases": []})
    scenario["ground_truth"]["claims"].append(
        _claim(
            key="c2",
            subject=subject,
            predicate=predicate,
            value="2026-06-01" if predicate == "deadline" else "active",
            contradicts_with=["c1"],
        )
    )

    with pytest.raises(GraphIntegrityError, match="same subject and predicate"):
        GraphWriter(db).ingest_document(scenario)

    assert db.writes == []


@pytest.mark.parametrize("relation", ["supersede", "contradict"])
def test_ingest_rechecks_existing_claim_scope_for_relations(relation):
    db = IntegrityDB(
        claims={
            "c1": {
                "id": graph_id("scenario:c1"),
                "subject": "project",
                "predicate": "status",
            },
            "c2": {
                "id": graph_id("scenario:c2"),
                "subject": "project",
                "predicate": "status",
            },
        }
    )
    first = _claim(key="c1", subject="person", predicate="status")
    second = _claim(key="c2", subject="person", predicate="status")
    if relation == "supersede":
        second["supersedes"] = "c1"
    else:
        second["contradicts_with"] = ["c1"]
    scenario = {
        "scenario_id": "scenario",
        "entities": [
            {"name": "person", "type": "person", "aliases": []},
        ],
        "ground_truth": {"claims": [first, second], "qa": []},
    }

    with pytest.raises(GraphIntegrityError, match="existing Claim.*scope"):
        GraphWriter(db).ingest_document(scenario)

    assert db.writes == []


def test_ingest_rejects_standalone_existing_claim_scope_before_any_write():
    db = IntegrityDB(
        claims={
            "c1": {
                "id": graph_id("scenario:c1"),
                "subject": "project",
                "predicate": "status",
            }
        }
    )
    scenario = _scenario()
    scenario["ground_truth"]["claims"][0]["subject"] = "person"
    scenario["entities"] = [{"name": "person", "type": "person", "aliases": []}]

    with pytest.raises(GraphIntegrityError, match="existing Claim.*scope"):
        GraphWriter(db).ingest_document(scenario)

    assert db.writes == []


def test_apply_plan_rejects_invalid_deadline_value_before_any_write():
    db = RecordingDB()
    plan = deepcopy(_plan())
    plan["create"][0].update(predicate="deadline", value="not-a-date")

    with pytest.raises(ValueError, match="value"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []
    assert db.queries == []


class IntegrityDB(RecordingDB):
    def __init__(self, claims=None, edges=()):
        super().__init__()
        self.claims = claims or {}
        self.edges = set(edges)

    def query_one(self, cypher):
        self.queries.append(cypher)
        if "MATCH (n:Claim" in cypher:
            claim_id = int(cypher.split("id: ", 1)[1].split("}", 1)[0])
            return {
                "c": int(claim_id in {value["id"] for value in self.claims.values()})
            }
        if "MATCH (c:Claim" in cypher and "[:ABOUT]" in cypher:
            claim_id = int(cypher.split("id: ", 1)[1].split("}", 1)[0])
            for claim in self.claims.values():
                if claim["id"] == claim_id:
                    return {
                        "id": claim_id,
                        "subject": claim["subject"],
                        "predicate": claim["predicate"],
                    }
            return None
        if "-[:SUPERSEDES]->" in cypher:
            parts = cypher.split("id: ")
            source_id = int(parts[1].split("}", 1)[0])
            target_id = int(parts[2].split("}", 1)[0])
            return {"c": int((source_id, target_id) in self.edges)}
        return {"c": 0}

    def query(self, cypher, consistency="causal"):
        self.queries.append(cypher)
        if "MATCH (a:Claim" in cypher and "[:SUPERSEDES]->" in cypher:
            source_id = int(cypher.split("id: ", 1)[1].split("}", 1)[0])
            return [
                {"new_id": source_id, "old_id": old_id}
                for new_id, old_id in sorted(self.edges)
                if new_id == source_id
            ]
        self.writes.append(cypher)
        return []


class FailureAfterStepDB:
    def __init__(self, fail_after=None):
        self.fail_after = fail_after
        self.operations = 0
        self.claims = set()
        self.evidence = set()
        self.sources = set()
        self.entities = set()
        self.claim_scopes = {}
        self.edges = set()

    def _step(self):
        self.operations += 1
        if self.fail_after is not None and self.operations == self.fail_after:
            raise RuntimeError("recording adapter failed after step")

    def query_one(self, cypher):
        import re

        if "RETURN c.subject AS subject" in cypher:
            claim_id = int(cypher.split("id: ", 1)[1].split("}", 1)[0])
            scope = self.claim_scopes.get(claim_id)
            return (
                {"subject": scope[0], "predicate": scope[1]}
                if scope is not None
                else None
            )
        edge = re.search(
            r"\(a:(\w+) \{id: (\d+)\}\)-\[:(\w+)\]->\(b:(\w+) \{id: (\d+)\}\)",
            cypher,
        )
        if edge:
            label_a, id_a, relation, label_b, id_b = edge.groups()
            return {
                "c": int(
                    (label_a, int(id_a), relation, label_b, int(id_b)) in self.edges
                )
            }
        node = re.search(r"\(n:(\w+) \{id: (\d+)\}\)", cypher)
        if node:
            label, node_id = node.groups()
            values = {
                "Claim": self.claims,
                "Evidence": self.evidence,
                "Source": self.sources,
                "Entity": self.entities,
            }[label]
            return {"c": int(int(node_id) in values)}
        return {"c": 0}

    def query(self, cypher, consistency="causal"):
        import re

        self._step()
        claim = re.search(
            r":Claim \{id: (?P<id>\d+), key: '[^']*', subject: '(?P<subject>[^']*)', "
            r"predicate: '(?P<predicate>[^']*)'",
            cypher,
        )
        if claim:
            self.claim_scopes[int(claim["id"])] = (
                claim["subject"],
                claim["predicate"],
            )
        node = re.search(r"\(\w+:(\w+) \{id: (\d+)", cypher)
        if node:
            label, node_id = node.groups()
            values = {
                "Claim": self.claims,
                "Evidence": self.evidence,
                "Source": self.sources,
                "Entity": self.entities,
            }[label]
            values.add(int(node_id))
        edge = re.search(
            r"\(\w+:(\w+) \{id: (\d+)\}\)-\[:(\w+)\]->\(\w+:(\w+) \{id: (\d+)\}\)",
            cypher,
        )
        if edge:
            label_a, id_a, relation, label_b, id_b = edge.groups()
            self.edges.add((label_a, int(id_a), relation, label_b, int(id_b)))
        return []


def test_apply_plan_retry_repairs_a_failure_after_a_partial_write():
    db = FailureAfterStepDB(fail_after=2)
    writer = GraphWriter(db)

    with pytest.raises(RuntimeError, match="failed after step"):
        writer.apply_plan(_plan(), "scenario")

    db.fail_after = None
    writer.apply_plan(_plan(), "scenario")

    claim_id = graph_id("scenario:c1")
    evidence_id = graph_id("scenario:c1:ev0")
    source_id = graph_id("slack:Asha Rao")
    entity_id = graph_id("scenario:project")
    assert claim_id in db.claims
    assert evidence_id in db.evidence
    assert source_id in db.sources
    assert entity_id in db.entities
    assert (
        "Claim",
        claim_id,
        "SUPPORTED_BY",
        "Evidence",
        evidence_id,
    ) in db.edges
    assert ("Evidence", evidence_id, "FROM", "Source", source_id) in db.edges
    assert ("Claim", claim_id, "ABOUT", "Entity", entity_id) in db.edges


def _integrity_claim(key, subject="project", predicate="status"):
    claim = _claim(key=key, subject=subject, predicate=predicate)
    claim["id"] = key
    return claim


def _integrity_plan(edges):
    return {
        "create": [],
        "supersede": [
            {"new_id": new_id, "old_id": old_id, "at": "2026-05-02"}
            for new_id, old_id in edges
        ],
        "contradict": [],
        "duplicates": 0,
        "warnings": [],
    }


def _contradiction_plan(edges):
    return {
        "create": [],
        "supersede": [],
        "contradict": [{"a_id": a_id, "b_id": b_id} for a_id, b_id in edges],
        "duplicates": 0,
        "warnings": [],
    }


def test_apply_plan_rejects_self_supersession_before_writes():
    db = IntegrityDB(
        claims={
            "c1": {"id": graph_id("c1"), "subject": "project", "predicate": "status"}
        }
    )
    plan = _integrity_plan([("c1", "c1")])

    with pytest.raises(GraphIntegrityError, match="self-supersession"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []


def test_apply_plan_rejects_self_contradiction_before_writes():
    db = IntegrityDB(
        claims={
            "c1": {"id": graph_id("c1"), "subject": "project", "predicate": "status"}
        }
    )
    plan = _contradiction_plan([("c1", "c1")])

    with pytest.raises(GraphIntegrityError, match="self-CONTRADICTS"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []


@pytest.mark.parametrize(
    ("a_scope", "b_scope"),
    [
        (("project", "status"), ("person", "status")),
        (("project", "status"), ("project", "deadline")),
    ],
    ids=("cross-subject", "cross-predicate"),
)
def test_apply_plan_rejects_cross_slot_contradiction_before_writes(a_scope, b_scope):
    db = IntegrityDB(
        claims={
            "a": {"id": graph_id("a"), "subject": a_scope[0], "predicate": a_scope[1]},
            "b": {"id": graph_id("b"), "subject": b_scope[0], "predicate": b_scope[1]},
        }
    )
    plan = _contradiction_plan([("a", "b")])

    with pytest.raises(GraphIntegrityError, match="same subject and predicate"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []


def test_apply_plan_rejects_cross_scope_supersession_before_writes():
    db = IntegrityDB(
        claims={
            "new": {"id": graph_id("new"), "subject": "project", "predicate": "status"},
            "old": {"id": graph_id("old"), "subject": "person", "predicate": "status"},
        }
    )
    plan = _integrity_plan([("new", "old")])

    with pytest.raises(GraphIntegrityError, match="same subject and predicate"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []


@pytest.mark.parametrize("relation", ["supersede", "contradict"])
def test_apply_plan_rechecks_existing_create_endpoint_scope(relation):
    db = IntegrityDB(
        claims={
            "new": {
                "id": graph_id("new"),
                "subject": "project",
                "predicate": "status",
            },
            "old": {
                "id": graph_id("old"),
                "subject": "project",
                "predicate": "status",
            },
        }
    )
    plan = {
        "create": [
            _claim(id="new", subject="person", predicate="status"),
            _claim(id="old", subject="person", predicate="status"),
        ],
        "supersede": (
            [{"new_id": "new", "old_id": "old", "at": "2026-05-02"}]
            if relation == "supersede"
            else []
        ),
        "contradict": (
            [{"a_id": "new", "b_id": "old"}] if relation == "contradict" else []
        ),
        "duplicates": 0,
        "warnings": [],
    }

    with pytest.raises(GraphIntegrityError, match="existing Claim.*scope"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []


def test_apply_plan_rejects_standalone_existing_claim_scope_before_any_write():
    db = IntegrityDB(
        claims={
            "c1": {
                "id": graph_id("scenario:c1"),
                "subject": "project",
                "predicate": "status",
            }
        }
    )
    plan = _plan()
    plan["create"][0]["subject"] = "person"

    with pytest.raises(GraphIntegrityError, match="existing Claim.*scope"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []


def test_apply_plan_rejects_cycle_using_existing_edges_before_writes():
    claims = {
        key: {"id": graph_id(key), "subject": "project", "predicate": "status"}
        for key in ("new", "old")
    }
    db = IntegrityDB(claims=claims, edges={(graph_id("old"), graph_id("new"))})
    plan = _integrity_plan([("new", "old")])

    with pytest.raises(GraphIntegrityError, match="cycle"):
        GraphWriter(db).apply_plan(plan, "scenario")

    assert db.writes == []
