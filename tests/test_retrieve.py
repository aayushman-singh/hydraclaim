import re

import pytest

from hydraclaim.retrieve import (
    abstain_message,
    abstain_uncovered_message,
    answer,
    build_chain_answer,
    build_conflict_answer,
    build_fast_answer,
    build_temporal_answer,
)

CLAIM = {
    "id": "deadline_drift:dl-3",
    "subject": "product launch",
    "predicate": "deadline",
    "value": "2026-10-17",
    "valid_from": "2026-05-18",
    "valid_to": None,
    "status": "active",
    "source_kind": "meeting",
    "author": "Meeting notes",
    "quote": "the launch deadline locks at October 17",
}


def test_fast_answer_cites_value_source_and_quote():
    text = build_fast_answer(CLAIM)
    assert "2026-10-17" in text
    assert "meeting/Meeting notes" in text
    assert "the launch deadline locks at October 17" in text


def test_chain_answer_lists_history_oldest_behind_current():
    chain = [
        {
            "id": "dl-2",
            "value": "2026-10-10",
            "valid_from": "2026-05-10",
            "valid_to": "2026-05-18",
            "hops": 1,
        },
        {
            "id": "dl-1",
            "value": "2026-10-03",
            "valid_from": "2026-05-05",
            "valid_to": "2026-05-10",
            "hops": 2,
        },
    ]
    text = build_chain_answer(CLAIM, chain)
    assert "current, since 2026-05-18" in text
    assert "2026-10-10 (2026-05-10 -> 2026-05-18)" in text
    assert "2026-10-03" in text


def test_temporal_answer_returns_previous_value():
    current = {**CLAIM, "valid_from": "2026-05-18"}
    previous = {
        "id": "dl-2",
        "subject": "product launch",
        "predicate": "deadline",
        "value": "2026-10-10",
        "valid_from": "2026-05-10",
        "valid_to": "2026-05-18",
        "source_kind": "meeting",
        "author": "Meeting notes",
        "quote": "deadline moved to Oct 10",
    }
    text = build_temporal_answer(current, previous)
    assert "Before the most recent change on 2026-05-18" in text
    assert "was 2026-10-10" in text
    assert "from 2026-05-10 to 2026-05-18" in text


def test_temporal_question_uses_previous_claim():
    claims = [
        _claim("dl-3", "2026-10-17", "2026-05-18"),
        _claim(
            "dl-2",
            "2026-10-10",
            "2026-05-10",
            status="superseded",
            valid_to="2026-05-18",
        ),
        _claim(
            "dl-1",
            "2026-10-03",
            "2026-05-05",
            status="superseded",
            valid_to="2026-05-10",
        ),
    ]
    db = _FakeDB([{"name": "product launch", "aliases": "launch"}], claims)
    result = answer(db, "What was the launch deadline before the most recent change?")
    assert "was 2026-10-10" in result["answer"]
    assert "2026-10-17" not in result["answer"]


def test_temporal_fallback_uses_chain_answer():
    claims = [
        _claim("dl-3", "2026-10-17", "2026-05-18"),
        _claim(
            "dl-2",
            "2026-10-10",
            "2026-05-10",
            status="superseded",
            valid_to="2026-05-18",
        ),
    ]
    db = _FakeDB(
        [{"name": "product launch", "aliases": "launch"}],
        claims,
        sup_edges=((claims[0]["id"], claims[1]["id"]),),
    )
    result = answer(db, "What was the launch deadline before the cut?")
    assert "current, since 2026-05-18" in result["answer"]
    assert "2026-10-10" in result["answer"]


def test_conflict_answer_shows_all_sides_and_winner():
    ranked = [
        (
            {
                **CLAIM,
                "id": "c3",
                "predicate": "owned_by",
                "value": "Priya Shah",
                "subject": "payments integration",
                "source_kind": "linear",
                "author": "Linear",
                "quote": "owner set to Priya Shah",
                "valid_from": "2026-05-21",
            },
            0.71,
        ),
        (
            {
                **CLAIM,
                "id": "c2",
                "predicate": "owned_by",
                "value": "Dario Kim",
                "subject": "payments integration",
                "source_kind": "slack",
                "author": "Dario Kim",
                "quote": "taking over the payments integration",
                "valid_from": "2026-05-14",
            },
            0.63,
        ),
    ]
    text = build_conflict_answer("payments integration", "owned_by", ranked)
    assert "Unresolved conflict" in text
    assert "Priya Shah" in text and "Dario Kim" in text
    assert "0.71" in text
    assert text.index("Priya Shah") < text.index("Dario Kim")  # winner first
    assert "never reconciled" in text


def test_abstain_message_names_what_was_searched():
    text = abstain_message("product launch", "budget")
    assert "budget" in text and "product launch" in text
    assert "not in the history" in text
    assert "searched" in abstain_message("coffee machine", None)


def test_abstain_uncovered_lists_tracked_predicates():
    text = abstain_uncovered_message("payments integration", ["owned_by", "status"])
    assert "owned_by, status" in text
    assert "not in the history" in text
    assert "payments integration" in text


def test_abstain_uncovered_falls_back_without_claims():
    assert abstain_uncovered_message("coffee machine", []) == abstain_message(
        "coffee machine", None
    )


class _FakeDB:
    """Serves canned rows for the query shapes answer()/probe() emit."""

    def __init__(self, entities, claims, sup_edges=(), con_edges=()):
        self._entities = entities  # {"name", "aliases"}
        self._claims = claims  # full fetch_claims row shape
        self._sup = sup_edges  # (new_id, old_id)
        self._con = con_edges  # (a_id, b_id, resolved)

    def query(self, cypher, consistency="causal"):
        if "MATCH (e:Entity)" in cypher:
            return list(self._entities)
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            match = re.search(r"c:Claim \{id: (\d+)\}", cypher)
            claim_id = int(match.group(1)) if match else None
            row = next(
                (claim for claim in self._claims if claim["id"] == claim_id), None
            )
            if row is None:
                return []
            return [
                {
                    "id": row["id"],
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                }
            ]
        if "SUPERSEDES*1..5" in cypher:
            m = re.search(r"id:\s*(\d+)", cypher)
            start = int(m.group(1)) if m else None
            ancestors = set()
            stack = [start]
            out = []
            while stack:
                cur = stack.pop()
                for new_id, old_id in self._sup:
                    if new_id == cur and old_id not in ancestors:
                        ancestors.add(old_id)
                        stack.append(old_id)
                        row = next(c for c in self._claims if c["id"] == old_id)
                        out.append(
                            {
                                "id": old_id,
                                "value": row["value"],
                                "valid_from": row["valid_from"],
                                "valid_to": row["valid_to"],
                                "subject": row["subject"],
                                "predicate": row["predicate"],
                            }
                        )
            return out
        if "-[:SUPERSEDES]->" in cypher and "RETURN older.id AS id" in cypher:
            match = re.search(r"(?:current|a):Claim \{id: (\d+)\}", cypher)
            source = int(match.group(1)) if match else None
            return [
                {
                    "id": old_id,
                    "value": next(
                        claim["value"]
                        for claim in self._claims
                        if claim["id"] == old_id
                    ),
                    "valid_from": next(
                        claim["valid_from"]
                        for claim in self._claims
                        if claim["id"] == old_id
                    ),
                    "valid_to": next(
                        claim["valid_to"]
                        for claim in self._claims
                        if claim["id"] == old_id
                    ),
                    "predicate": next(
                        claim["predicate"]
                        for claim in self._claims
                        if claim["id"] == old_id
                    ),
                }
                for new_id, old_id in self._sup
                if new_id == source
            ]
        if "-[:SUPERSEDES]->" in cypher:
            match = re.search(r"a:Claim \{id: (\d+)\}", cypher)
            source = int(match.group(1)) if match else None
            return [{"new_id": a, "old_id": b} for a, b in self._sup if a == source]
        if "-[r:CONTRADICTS]->" in cypher:
            match = re.search(r"a:Claim \{id: (\d+)\}", cypher)
            source = int(match.group(1)) if match else None
            return [
                {"a_id": a, "b_id": b, "resolved": r}
                for a, b, r in self._con
                if a == source
            ]
        if "c.key AS key" in cypher:  # fetch_claims (superset of probe cols)
            return self._filter(cypher)
        if "c.status AS status" in cypher:  # probe
            return [
                {"id": c["id"], "status": c["status"], "value": c["value"]}
                for c in self._filter(cypher)
            ]
        raise AssertionError(f"unexpected query: {cypher[:120]}")

    def _filter(self, cypher):
        rows = list(self._claims)
        if "c.status = 'active'" in cypher:
            rows = [c for c in rows if c["status"] == "active"]
        return rows


def _claim(
    key,
    value,
    valid_from,
    status="active",
    valid_to="",
    predicate="deadline",
    subject="product launch",
):
    return {
        "id": abs(hash(key)) % (2**62),
        "key": key,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "status": status,
        "confidence": 0.9,
        "quote": f"quote for {key}",
        "explicitness": 1.0,
        "extraction_confidence": 0.9,
        "source_kind": "meeting",
        "author": "Meeting notes",
    }


def test_origin_question_answers_with_earliest_claim():
    claims = [
        _claim("dl-3", "2026-10-17", "2026-05-18"),
        _claim(
            "dl-2",
            "2026-10-10",
            "2026-05-10",
            status="superseded",
            valid_to="2026-05-18",
        ),
        _claim(
            "dl-1",
            "2026-10-03",
            "2026-05-05",
            status="superseded",
            valid_to="2026-05-10",
        ),
    ]
    db = _FakeDB([{"name": "product launch", "aliases": "launch"}], claims)
    result = answer(db, "When was the launch deadline first set?")
    assert "2026-10-03" in result["answer"]
    assert "2026-05-05" in result["answer"]
    assert "2026-10-17" not in result["answer"]


def test_unmapped_predicate_abstains_and_names_tracked_predicates():
    claims = [
        _claim(
            "pay-own-2",
            "Dario Kim",
            "2026-05-14",
            predicate="owned_by",
            subject="payments integration",
        ),
    ]
    db = _FakeDB([{"name": "payments integration", "aliases": "payments"}], claims)
    result = answer(db, "What is the payments integration's uptime SLA?")
    assert result["route"] == "ABSTAIN"
    assert "owned_by" in result["answer"]
    assert "Dario Kim" not in result["answer"]


def test_answer_propagates_llm_classification_error():
    claims = [
        _claim(
            "pay-own-2",
            "Dario Kim",
            "2026-05-14",
            predicate="owned_by",
            subject="payments integration",
        )
    ]
    db = _FakeDB([{"name": "payments integration", "aliases": "payments"}], claims)

    def broken(_question):
        raise RuntimeError("classifier unavailable")

    with pytest.raises(RuntimeError, match="classifier unavailable"):
        answer(db, "Who owns payments?", classification_mode="llm", llm_fn=broken)
