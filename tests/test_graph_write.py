from __future__ import annotations

from copy import deepcopy

import pytest

from hydraclaim import ingest, reconcile
from hydraclaim.graph_write import GraphWriter
from hydraclaim.model import graph_id


class RecordingDB:
    def __init__(self):
        self.queries = []
        self.writes = []

    def query(self, cypher):
        self.queries.append(cypher)
        self.writes.append(cypher)
        return []

    def query_one(self, cypher):
        self.queries.append(cypher)
        return {"c": 0}


class IdempotentDB(RecordingDB):
    def __init__(self):
        super().__init__()
        self.claim_ids = set()

    def query(self, cypher):
        result = super().query(cypher)
        if "SUPPORTED_BY" in cypher:
            self.claim_ids.add(graph_id("scenario:c1"))
        return result

    def query_one(self, cypher):
        self.queries.append(cypher)
        if "(n:Claim" in cypher:
            claim_id = int(cypher.split("id: ", 1)[1].split("}", 1)[0])
            return {"c": int(claim_id in self.claim_ids)}
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
