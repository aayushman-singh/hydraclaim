from datetime import datetime, timezone

from trustgraph.probe import ProbeResult
from trustgraph.router import (
    ROUTE_ABSTAIN,
    ROUTE_DEEP,
    ROUTE_FAST,
    classify,
    decide_route,
    heuristic_classify,
)

ROSTER = [
    {"name": "payments integration", "type": "system", "aliases": ["payments"]},
    {"name": "Priya Shah", "type": "person", "aliases": ["Priya"]},
    {"name": "product launch", "type": "project", "aliases": ["launch", "the launch"]},
]

NOW = datetime(2026, 5, 25, tzinfo=timezone.utc)


def _probe(**kw):
    defaults = dict(subject="payments integration", predicate="owned_by",
                    coverage=2, conflicts=0, distinct_active_values=1, chain_depth=0)
    defaults.update(kw)
    return ProbeResult(**defaults)


def test_heuristic_ownership_question():
    cls = heuristic_classify("Who owns the payments integration?", ROSTER, NOW)
    assert cls.subject == "payments integration"  # via exact name
    assert cls.predicate == "owned_by"
    assert cls.question_type == "lookup"


def test_heuristic_resolves_alias_subject():
    cls = heuristic_classify("Who owns payments?", ROSTER, NOW)
    assert cls.subject == "payments integration"


def test_heuristic_deadline_and_update_markers():
    cls = heuristic_classify("What is the current launch deadline?", ROSTER, NOW)
    assert cls.subject == "product launch"
    assert cls.predicate == "deadline"
    assert cls.question_type == "knowledge_update"


def test_heuristic_temporal_marker_sets_as_of():
    cls = heuristic_classify("What was the launch deadline last week?", ROSTER, NOW)
    assert cls.question_type == "temporal"
    assert cls.as_of == "2026-05-18"


def test_heuristic_unknown_subject():
    cls = heuristic_classify("Who owns the coffee machine?", ROSTER, NOW)
    assert cls.subject is None


def test_route_abstains_on_zero_coverage():
    assert decide_route("lookup", _probe(coverage=0)) == ROUTE_ABSTAIN


def test_route_fast_on_clean_lookup():
    assert decide_route("lookup", _probe()) == ROUTE_FAST


def test_route_deep_on_conflict_edges():
    assert decide_route("lookup", _probe(conflicts=1)) == ROUTE_DEEP


def test_route_deep_on_distinct_active_values():
    assert decide_route("lookup", _probe(distinct_active_values=2)) == ROUTE_DEEP


def test_route_deep_on_supersession_chain():
    assert decide_route("lookup", _probe(chain_depth=2)) == ROUTE_DEEP


def test_route_deep_on_temporal_even_when_clean():
    assert decide_route("temporal", _probe()) == ROUTE_DEEP


def test_classify_uses_llm_output_when_valid():
    cls = classify(
        "Who owns the payments integration?",
        ROSTER,
        llm_fn=lambda q: {"subject": "payments", "predicate": "owned_by",
                          "question_type": "lookup", "as_of": None},
        now=NOW,
    )
    assert cls.subject == "payments integration"  # canonicalized from alias
    assert cls.predicate == "owned_by"


def test_classify_falls_back_on_bad_llm_output():
    cls = classify(
        "Who owns the payments integration?",
        ROSTER,
        llm_fn=lambda q: {"subject": None, "predicate": "not-a-predicate",
                          "question_type": "weird"},
        now=NOW,
    )
    assert cls.subject == "payments integration"  # heuristic rescued it
    assert cls.predicate == "owned_by"
    assert cls.question_type == "lookup"


def test_classify_falls_back_when_llm_raises():
    def boom(q):
        raise RuntimeError("provider down")

    cls = classify("Who owns the payments integration?", ROSTER, llm_fn=boom, now=NOW)
    assert cls.predicate == "owned_by"
