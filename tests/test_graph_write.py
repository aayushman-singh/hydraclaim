from __future__ import annotations

import pytest

from hydraclaim.graph_write import GraphWriter


class RecordingDB:
    def __init__(self):
        self.queries = []

    def query(self, cypher):
        self.queries.append(cypher)
        return []

    def query_one(self, cypher):
        self.queries.append(cypher)
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

    GraphWriter(db).ingest_document(scenario)

    claim_index = next(
        i
        for i, query in enumerate(db.queries)
        if ":Claim" in query and "SUPPORTED_BY" in query
    )
    relation_index = next(
        i for i, query in enumerate(db.queries) if ":SUPERSEDES" in query
    )
    assert claim_index < relation_index


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
