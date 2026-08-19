"""Offline tests for the benchmark harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydraclaim.benchmark import (
    CountingDB,
    correct,
    router_only_route,
    run_arm,
    summarize,
)
from hydraclaim.router import ROUTE_DEEP, ROUTE_FAST


def test_router_only_route_maps_lookup_to_fast():
    assert router_only_route("lookup") == ROUTE_FAST


def test_router_only_route_maps_other_types_to_deep():
    for qtype in ("temporal", "conflict", "knowledge_update", "multi_session", "abstention"):
        assert router_only_route(qtype) == ROUTE_DEEP


def test_correct_abstention_when_route_is_abstain():
    assert correct({"route": "ABSTAIN", "answer": "declined"}, "ABSTAIN", "abstention") is True


def test_correct_abstention_when_answered_is_false():
    assert correct({"route": "FAST", "answer": "some answer"}, "ABSTAIN", "abstention") is False


def test_correct_substring_match_ignores_case_and_punctuation():
    result = {"route": "FAST", "answer": "The owner is Priya Shah."}
    assert correct(result, "priya shah", "lookup") is True


def test_correct_substring_match_finds_date_inside_sentence():
    result = {"route": "DEEP", "answer": "The deadline moved to 2026-10-17 per the latest update."}
    assert correct(result, "2026-10-17", "knowledge_update") is True


def test_correct_mismatch_returns_false():
    result = {"route": "FAST", "answer": "The owner is Lee Park."}
    assert correct(result, "Priya Shah", "lookup") is False


class _StubDB:
    def __init__(self) -> None:
        self.closed = False

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        return [{"n": 1}]

    def query_one(self, cypher: str, consistency: str = "causal") -> dict | None:
        return {"n": 1}

    def node_exists(self, label: str, node_id: str) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def test_counting_db_forwards_calls_and_counts():
    stub = _StubDB()
    db = CountingDB(stub)

    db.reset()
    assert db.count == 0

    assert db.query("MATCH (n) RETURN n") == [{"n": 1}]
    assert db.query_one("MATCH (n) RETURN n LIMIT 1") == {"n": 1}
    assert db.node_exists("Entity", "x") is True
    assert db.count == 3

    db.close()
    assert stub.closed is True


def test_counting_db_context_manager_closes():
    stub = _StubDB()
    with CountingDB(stub) as db:
        db.query("MATCH (n) RETURN n")
    assert stub.closed is True


def test_summarize_renders_per_arm_rows_and_abstention_columns():
    arm_results = [
        {
            "arm": "router+probe",
            "questions": 4,
            "per_qtype": {
                "lookup": {"n": 2, "correct": 2},
                "abstention": {"n": 2, "correct": 1},
            },
            "abstention": {"tp": 1, "fp": 0, "fn": 1},
            "latency_ms": [10.0, 20.0, 30.0, 40.0],
            "queries_per_question": [1, 2, 3, 4],
        },
        {
            "arm": "router-only",
            "questions": 4,
            "per_qtype": {
                "lookup": {"n": 2, "correct": 2},
                "abstention": {"n": 2, "correct": 0},
            },
            "abstention": {"tp": 0, "fp": 0, "fn": 2},
            "latency_ms": [5.0, 15.0, 25.0, 35.0],
            "queries_per_question": [1, 1, 1, 1],
        },
    ]
    table = summarize(arm_results)

    assert "router+probe" in table
    assert "router-only" in table
    assert "abstention P" in table
    assert "abstention R" in table
    assert "queries/q" in table
    assert "p50 ms" in table
    assert "p95 ms" in table
    assert "lookup" in table
    assert "abstention" in table


def test_run_arm_uses_forced_route_for_always_deep():
    """run_arm should route all questions DEEP in the always-deep arm.

    With a stub DB that reports no coverage, every question returns ABSTAIN
    regardless of the forced route, but the recorded route still reflects it.
    """

    class _CoverageDB:
        def __init__(self) -> None:
            self._count = 0

        def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
            self._count += 1
            return []

        def query_one(self, cypher: str, consistency: str = "causal") -> dict | None:
            self._count += 1
            return None

        def node_exists(self, label: str, node_id: str) -> bool:
            self._count += 1
            return False

        def close(self) -> None:
            pass

    scenarios = [
        {
            "ground_truth": {
                "qa": [
                    {"question": "Who owns payments?", "answer": "Priya Shah", "qtype": "lookup"},
                ]
            }
        }
    ]
    db = CountingDB(_CoverageDB())
    result = run_arm(db, scenarios, "always-deep")

    assert result["arm"] == "always-deep"
    assert result["questions"] == 1
    assert result["per_qtype"]["lookup"]["n"] == 1


def test_run_arm_router_only_treats_refusal_as_abstain():
    class _RefusalDB:
        def __init__(self) -> None:
            self._count = 0

        def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
            self._count += 1
            return []

        def query_one(self, cypher: str, consistency: str = "causal") -> dict | None:
            self._count += 1
            return None

        def node_exists(self, label: str, node_id: str) -> bool:
            self._count += 1
            return False

        def close(self) -> None:
            pass

    scenarios = [
        {
            "entities": [{"name": "payments integration", "aliases": ["payments"]}],
            "ground_truth": {
                "qa": [
                    {
                        "question": "Who owns the coffee machine?",
                        "answer": "ABSTAIN",
                        "qtype": "abstention",
                    },
                ]
            },
        }
    ]
    db = CountingDB(_RefusalDB())
    result = run_arm(db, scenarios, "router-only")

    assert result["abstention"]["tp"] == 1
    assert result["abstention"]["fn"] == 0
    assert result["per_qtype"]["abstention"]["correct"] == 1


def test_correct_with_rubric_requires_all_items():
    result = {
        "route": "DEEP",
        "answer": ("Unresolved conflict about payments integration — owned_by:\n"
                   "  - Priya Shah — linear/Linear, 2026-05-21\n"
                   "  - Dario Kim — slack/Dario Kim, 2026-05-14\n"
                   "The highest-trust record says Priya Shah."),
    }
    rubric = ["unresolved conflict", "Dario Kim", "Priya Shah"]
    assert correct(result, "a long gold sentence that never appears verbatim",
                   "conflict", rubric)


def test_correct_with_rubric_rejects_partial_coverage():
    result = {
        "route": "FAST",
        "answer": "payments integration — owned_by: Priya Shah (as of 2026-05-21)",
    }
    rubric = ["unresolved conflict", "Dario Kim", "Priya Shah"]
    assert not correct(result, "gold", "conflict", rubric)


def test_correct_without_rubric_ignores_rubric_logic():
    result = {"route": "FAST", "answer": "The deadline is 2026-10-17."}
    assert correct(result, "2026-10-17", "knowledge_update")


def test_naive_answer_abstains_when_subject_missing():
    from hydraclaim.benchmark import naive_answer, ROUTE_NAIVE_RAG

    class _EmptyDB:
        def query(self, cypher, consistency="causal"):
            return []

    roster = [{"name": "product launch", "aliases": ["launch"]}]
    result = naive_answer(CountingDB(_EmptyDB()), "What is the coffee budget?", roster)
    assert result["route"] == ROUTE_NAIVE_RAG
    assert "No subject matched" in result["answer"]


def test_naive_answer_returns_top_overlapping_claim():
    from hydraclaim.benchmark import naive_answer, ROUTE_NAIVE_RAG

    class _ClaimDB:
        def query(self, cypher, consistency="causal"):
            return [
                {"predicate": "deadline", "value": "2026-10-17", "status": "active", "valid_from": "2026-05-18"},
                {"predicate": "deadline", "value": "2026-10-03", "status": "active", "valid_from": "2026-05-05"},
            ]

    roster = [{"name": "product launch", "aliases": ["launch"]}]
    result = naive_answer(CountingDB(_ClaimDB()), "What is the current launch deadline?", roster)
    assert result["route"] == ROUTE_NAIVE_RAG
    assert "2026-10-17" in result["answer"]


def test_naive_answer_abstains_when_no_active_claims():
    from hydraclaim.benchmark import naive_answer, ROUTE_NAIVE_RAG

    class _EmptyClaimDB:
        def query(self, cypher, consistency="causal"):
            return []

    roster = [{"name": "product launch", "aliases": ["launch"]}]
    result = naive_answer(CountingDB(_EmptyClaimDB()), "What is the launch deadline?", roster)
    assert result["route"] == ROUTE_NAIVE_RAG
    assert "No active claims" in result["answer"]
