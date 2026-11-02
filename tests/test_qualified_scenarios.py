from __future__ import annotations

from types import SimpleNamespace

import pytest

from hydraclaim import benchmark
from hydraclaim.generate.generator import expand_scenario
from hydraclaim.generate.scenarios import multi_vendor_decision, prefers_shift
from hydraclaim.graph_write import _validate_document


@pytest.mark.parametrize("build", [multi_vendor_decision, prefers_shift])
def test_generated_qualified_scenario_has_source_and_qa_scope(build):
    spec = build()
    doc = expand_scenario(spec, seed=42)
    claims = {claim["key"]: claim for claim in doc["ground_truth"]["claims"]}
    messages = [
        message for session in doc["sessions"] for message in session["messages"]
    ]

    qualified = [
        claim
        for claim in claims.values()
        if claim["predicate"] in {"decided", "prefers"}
    ]
    assert qualified
    for claim in qualified:
        assert claim["qualifier"]
        assert claim["qualifier"] in claim["quote"].casefold()
        assert any(
            message["msg_id"] == claim["msg_id"] and claim["quote"] in message["text"]
            for message in messages
        )

    for qa in doc["ground_truth"]["qa"]:
        gold = [claims[key] for key in qa["gold_claim_keys"]]
        gold_qualifiers = {
            claim["qualifier"]
            for claim in gold
            if claim["predicate"] in {"decided", "prefers"}
        }
        if gold_qualifiers:
            assert len(gold_qualifiers) == 1
            assert qa["qualifier"] == next(iter(gold_qualifiers))
        else:
            assert "qualifier" not in qa

    assert _validate_document(doc) == []


class BenchmarkDB:
    def __init__(self):
        self.count = 0

    def reset(self):
        self.count = 0


@pytest.mark.parametrize(
    "arm", ["router+probe", "router-only", "always-deep", "naive-rag"]
)
def test_benchmark_forwards_qa_qualifier_to_reader(monkeypatch, arm):
    received = {"answer": [], "scope": []}

    class Reader:
        def __init__(self, db):
            pass

        def answer(self, question, **kwargs):
            received["answer"].append(kwargs.get("qualifier"))
            return SimpleNamespace(
                as_dict=lambda: {"route": "FAST", "answer": "SendGrid"}
            )

        def read_claims(self, scope):
            received["scope"].append(scope)
            return ()

    monkeypatch.setattr(benchmark, "ClaimReader", Reader)
    monkeypatch.setattr(
        benchmark,
        "classify",
        lambda question, roster: SimpleNamespace(
            subject="email platform", predicate="decided", question_type="lookup"
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "fetch_entities",
        lambda db: [{"name": "email platform", "aliases": ["email"]}],
    )
    scenario = {
        "ground_truth": {
            "qa": [
                {
                    "question": "Which email platform was decided on?",
                    "qualifier": "email platform",
                    "answer": "SendGrid",
                    "qtype": "lookup",
                    "gold_claim_keys": ["mv-1"],
                }
            ]
        }
    }

    result = benchmark.run_arm(BenchmarkDB(), [scenario], arm, judge=lambda *_: True)

    assert result["questions"] == 1
    if arm == "naive-rag":
        assert received["scope"][0].qualifier == "email platform"
        assert received["scope"][0].predicate == "decided"
    else:
        assert received["answer"] == ["email platform"]
