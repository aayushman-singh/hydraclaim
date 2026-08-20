from __future__ import annotations

from datetime import datetime, timezone
import re

import pytest

from hydraclaim.claim_read import ClaimReadLimitError, ClaimReader, ClaimScope
from hydraclaim.errors import GraphIntegrityError


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


class _FakeDB:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "MATCH (e:Entity)" in cypher:
            return [{"name": "product launch", "aliases": "launch"}]
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            return [{"id": 1, "subject": "product launch", "predicate": "owned_by"}]
        if "c.key AS key" in cypher:
            return [
                {
                    "id": 1,
                    "key": "claim-1",
                    "subject": "product launch",
                    "predicate": "owned_by",
                    "value": "Priya Shah",
                    "valid_from": "2026-08-01",
                    "valid_to": "",
                    "status": "active",
                }
            ]
        return []


def _claim(
    claim_id: int,
    key: str,
    value: str,
    *,
    status: str = "active",
    predicate: str = "deadline",
    subject: str = "product launch",
    valid_from: str = "2026-08-01",
) -> dict:
    return {
        "id": claim_id,
        "key": key,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "valid_from": valid_from,
        "valid_to": "",
        "status": status,
        "confidence": 0.9,
        "quote": key,
        "explicitness": 1.0,
        "extraction_confidence": 0.9,
        "source_kind": "meeting",
        "author": "Meeting notes",
    }


class _OrderingDB:
    def __init__(self, claims: list[dict], chain: list[dict] | None = None) -> None:
        self.claims = claims
        self.chain = chain or []
        self.queries: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "MATCH (e:Entity)" in cypher:
            return [{"name": "product launch", "aliases": "launch"}]
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            memberships = [
                {"id": 10, "subject": "product launch", "predicate": "deadline"},
                *[
                    {
                        "id": row["id"],
                        "subject": row["subject"],
                        "predicate": row["predicate"],
                    }
                    for row in self.chain
                ],
            ]
            return [row for row in memberships if f"{{id: {row['id']}}}" in cypher]
        if "-[:SUPERSEDES]->" in cypher:
            return list(self.chain) if "{id: 10}" in cypher else []
        if "SUPERSEDES*1..5" in cypher:
            return list(self.chain)
        if "c.key AS key" in cypher:
            return list(self.claims)
        if "SUPERSEDES" in cypher or "CONTRADICTS" in cypher:
            return []
        return []


class _RelationDB:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            if "{id: 1}" in cypher:
                return [{"id": 1, "subject": "product launch", "predicate": "deadline"}]
            if "{id: 2}" in cypher:
                return [{"id": 2, "subject": "product launch", "predicate": "deadline"}]
            return []
        if "{id: 1}" not in cypher:
            return []
        return [
            {"new_id": 1, "old_id": 2},
            {"new_id": 1, "old_id": 99},
        ]


class _HighFanoutRelationDB(_RelationDB):
    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            claim_id = int(cypher.split("{id: ", 1)[1].split("}", 1)[0])
            return [
                {"id": claim_id, "subject": "product launch", "predicate": "deadline"}
            ]
        if "{id: 1}" in cypher:
            return [
                {"new_id": 1, "old_id": 2},
                {"new_id": 1, "old_id": 3},
                {"new_id": 1, "old_id": 4},
            ]
        return []


class _LimitDB:
    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        if "c.key AS key" not in cypher:
            return []
        return [
            _claim(1, "claim-1", "one"),
            _claim(2, "claim-2", "two"),
            _claim(3, "claim-3", "three"),
        ]


class _CrossScopeChainDB:
    def __init__(self, start: dict | None = None) -> None:
        self.queries: list[str] = []
        self.start = start or {
            "id": 10,
            "subject": "product launch",
            "predicate": "deadline",
        }

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "SUPERSEDES*1..5" in cypher:
            return [
                {
                    "id": 11,
                    "value": "target old",
                    "valid_from": "2026-08-01",
                    "valid_to": "",
                    "subject": "product launch",
                    "predicate": "deadline",
                },
                {
                    "id": 12,
                    "value": "other subject",
                    "valid_from": "2026-08-02",
                    "valid_to": "",
                    "subject": "other project",
                    "predicate": "deadline",
                },
                {
                    "id": 13,
                    "value": "other predicate",
                    "valid_from": "2026-08-03",
                    "valid_to": "",
                    "subject": "product launch",
                    "predicate": "status",
                },
            ]
        if "-[:SUPERSEDES]->" in cypher:
            if "{id: 10}" in cypher:
                return [
                    {
                        "id": 11,
                        "value": "target old",
                        "valid_from": "2026-08-01",
                        "valid_to": "",
                        "predicate": "deadline",
                    },
                    {
                        "id": 12,
                        "value": "other subject",
                        "valid_from": "2026-08-02",
                        "valid_to": "",
                        "predicate": "deadline",
                    },
                    {
                        "id": 13,
                        "value": "other predicate",
                        "valid_from": "2026-08-03",
                        "valid_to": "",
                        "predicate": "status",
                    },
                ]
            return []
        if "{id: 10}" in cypher:
            return [self.start]
        if "{id: 11}" in cypher:
            return [{"id": 11, "subject": "product launch", "predicate": "deadline"}]
        if "{id: 12}" in cypher:
            return [{"id": 12, "subject": "other project", "predicate": "deadline"}]
        if "{id: 13}" in cypher:
            return [{"id": 13, "subject": "product launch", "predicate": "status"}]
        return []


class _IterativeChainDB:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            if "{id: 10}" in cypher:
                return [
                    {"id": 10, "subject": "product launch", "predicate": "deadline"}
                ]
            if "{id: 11}" in cypher:
                return [{"id": 11, "subject": "other project", "predicate": "deadline"}]
            if "{id: 12}" in cypher:
                return [
                    {"id": 12, "subject": "product launch", "predicate": "deadline"}
                ]
            return []
        if "SUPERSEDES*1..5" in cypher:
            return [
                {
                    "id": 11,
                    "value": "middle",
                    "valid_from": "2026-08-02",
                    "valid_to": "",
                    "predicate": "deadline",
                },
                {
                    "id": 12,
                    "value": "terminal",
                    "valid_from": "2026-08-01",
                    "valid_to": "",
                    "predicate": "deadline",
                },
            ]
        if "-[:SUPERSEDES]->" in cypher and "{id: 10}" in cypher:
            return [
                {
                    "id": 11,
                    "value": "middle",
                    "valid_from": "2026-08-02",
                    "valid_to": "",
                    "predicate": "deadline",
                }
            ]
        if "-[:SUPERSEDES]->" in cypher and "{id: 11}" in cypher:
            return [
                {
                    "id": 12,
                    "value": "terminal",
                    "valid_from": "2026-08-01",
                    "valid_to": "",
                    "predicate": "deadline",
                }
            ]
        return []


class _CycleChainDB:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            claim_id = 10 if "{id: 10}" in cypher else 11
            return [
                {"id": claim_id, "subject": "product launch", "predicate": "deadline"}
            ]
        if "-[:SUPERSEDES]->" not in cypher:
            return []
        if "{id: 10}" in cypher:
            return [
                {
                    "id": 11,
                    "value": "old",
                    "valid_from": "2026-08-01",
                    "valid_to": "",
                    "predicate": "deadline",
                }
            ]
        return [
            {
                "id": 10,
                "value": "current",
                "valid_from": "2026-08-02",
                "valid_to": "",
                "predicate": "deadline",
            }
        ]


class _BranchingChainDB:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.claims = {
            10: ("head", "2026-08-04"),
            11: ("branch-a", "2026-08-03"),
            12: ("branch-b", "2026-08-02"),
            13: ("shared-ancestor", "2026-08-01"),
        }
        self.edges = {10: (11, 12), 11: (13,), 12: (13,)}

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            claim_id = int(cypher.split("{id: ", 1)[1].split("}", 1)[0])
            if claim_id in self.claims:
                return [
                    {
                        "id": claim_id,
                        "subject": "product launch",
                        "predicate": "deadline",
                    }
                ]
            return []
        if "-[:SUPERSEDES]->" in cypher:
            claim_id = int(cypher.split("{id: ", 1)[1].split("}", 1)[0])
            return [
                {
                    "id": older_id,
                    "value": self.claims[older_id][0],
                    "valid_from": self.claims[older_id][1],
                    "valid_to": "",
                    "predicate": "deadline",
                }
                for older_id in self.edges.get(claim_id, ())
            ]
        return []


class _AsOfDB:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.claims = [
            {
                "id": 1,
                "key": "old",
                "subject": "product launch",
                "predicate": "deadline",
                "value": "2026-10-01",
                "valid_from": "2026-07-01",
                "valid_to": "",
                "status": "active",
                "recorded_at": "2026-07-01T00:00:00+00:00",
            },
            {
                "id": 2,
                "key": "future",
                "subject": "product launch",
                "predicate": "deadline",
                "value": "2026-11-01",
                "valid_from": "2026-09-01",
                "valid_to": "",
                "status": "active",
                "recorded_at": "2026-09-01T00:00:00+00:00",
            },
        ]

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "MATCH (e:Entity)" in cypher:
            return [{"id": 100, "name": "product launch", "aliases": "launch"}]
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            match = re.search(r"c:Claim \{id: (\d+)\}", cypher)
            claim_id = int(match.group(1)) if match else None
            return (
                [
                    {
                        "id": claim_id,
                        "subject": "product launch",
                        "predicate": "deadline",
                    }
                ]
                if claim_id in {claim["id"] for claim in self.claims}
                else []
            )
        if "c.key AS key" in cypher:
            rows = list(self.claims)
            if "c.recorded_at <= '2026-08-01'" in cypher:
                rows = [row for row in rows if row["recorded_at"] <= "2026-08-01"]
            if "c.status = 'active'" in cypher:
                rows = [row for row in rows if row["status"] == "active"]
            return rows
        if "SUPERSEDES" in cypher or "CONTRADICTS" in cypher:
            return []
        raise AssertionError(f"unexpected query: {cypher[:120]}")


def test_answer_returns_structured_abstention():
    result = ClaimReader(_FakeDB()).answer("Who owns unknown?", now=NOW)

    assert result.route == "ABSTAIN"
    assert result.citations == ()
    assert result.classification is not None


def test_historical_answer_excludes_future_recorded_claims():
    db = _AsOfDB()
    result = ClaimReader(db).answer(
        "What is the launch deadline?",
        classification_mode="llm",
        llm_fn=lambda question: {
            "subject": "product launch",
            "predicate": "deadline",
            "question_type": "lookup",
            "as_of": "2026-08-01",
        },
    )

    assert result.citations[0].value == "2026-10-01"
    assert "2026-11-01" not in result.text
    claim_reads = [query for query in db.queries if "c.key AS key" in query]
    assert claim_reads
    assert all("2026-08-01" in query for query in claim_reads)


def test_probe_queries_limit_relations_to_selected_claims():
    db = _FakeDB()
    ClaimReader(db).answer("Who owns launch?", now=NOW)

    relation_queries = [
        query for query in db.queries if "SUPERSEDES" in query or "CONTRADICTS" in query
    ]
    assert relation_queries
    assert all("a:Claim {id:" in query for query in relation_queries)
    assert any("ABOUT" in query and "e:Entity {name:" in query for query in db.queries)


def test_claim_reads_order_equal_dates_by_claim_id():
    db = _OrderingDB([_claim(1, "claim-1", "2026-10-01")])

    ClaimReader(db).read_claims(ClaimScope("product launch", "deadline"))

    assert any("ORDER BY c.valid_from DESC, c.id DESC" in query for query in db.queries)


def test_equal_timestamp_active_selection_is_stable():
    db = _OrderingDB(
        [
            _claim(1, "claim-1", "2026-10-01"),
            _claim(2, "claim-2", "2026-10-01"),
        ]
    )

    result = ClaimReader(db).answer("What is the launch deadline?", now=NOW)

    assert result.route == "FAST"
    assert result.citations[0].claim_id == "claim-2"


def test_equal_timestamp_temporal_result_uses_stable_history_order():
    db = _OrderingDB(
        [
            _claim(
                1,
                "claim-1",
                "2026-10-01",
                status="superseded",
                valid_from="2026-08-01",
            ),
            _claim(2, "claim-2", "2026-10-02", valid_from="2026-08-01"),
        ]
    )

    result = ClaimReader(db).answer(
        "What was the launch deadline before the most recent change?", now=NOW
    )

    assert "was 2026-10-01" in result.text
    assert [citation.claim_id for citation in result.citations] == [
        "claim-2",
        "claim-1",
    ]


def test_equal_timestamp_conflict_citations_use_stable_claim_order():
    db = _OrderingDB(
        [
            _claim(1, "claim-1", "2026-10-01", valid_from="2026-08-01"),
            _claim(2, "claim-2", "2026-10-02", valid_from="2026-08-01"),
        ]
    )

    result = ClaimReader(db).answer("What is the launch deadline?", now=NOW)

    assert tuple(citation.claim_id for citation in result.citations) == (
        "claim-2",
        "claim-1",
    )


def test_chain_scope_constrains_start_and_older_claims_and_orders_ties():
    db = _OrderingDB(
        [],
        chain=[
            {
                "id": 1,
                "value": "old-1",
                "valid_from": "2026-08-01",
                "valid_to": "",
                "subject": "product launch",
                "predicate": "deadline",
            },
            {
                "id": 2,
                "value": "old-2",
                "valid_from": "2026-08-01",
                "valid_to": "",
                "subject": "product launch",
                "predicate": "deadline",
            },
            {
                "id": 3,
                "value": "other subject",
                "valid_from": "2026-08-01",
                "valid_to": "",
                "subject": "other subject",
                "predicate": "deadline",
            },
            {
                "id": 4,
                "value": "other predicate",
                "valid_from": "2026-08-01",
                "valid_to": "",
                "subject": "product launch",
                "predicate": "status",
            },
        ],
    )

    chain = ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))

    assert [row["id"] for row in chain] == [2, 1]
    query = next(query for query in db.queries if "-[:SUPERSEDES]->" in query)
    assert "current:Claim {id: 10}" in query
    assert "start_e.name" not in query
    assert "current.predicate" in query
    assert "older.predicate" in query
    assert "ORDER BY older.valid_from DESC, older.id DESC" in query
    assert query.count("MATCH") == 3
    assert " IN [" not in query
    assert ", (" not in query
    assert "SUPERSEDES*" not in query


def test_relation_reads_filter_unselected_endpoints():
    db = _RelationDB()

    relations = ClaimReader(db).read_relations(
        ClaimScope("product launch", "deadline"), {1, 2}, "SUPERSEDES"
    )

    assert relations == ({"new_id": 1, "old_id": 2},)
    assert db.queries
    relation_queries = [query for query in db.queries if "SUPERSEDES" in query]
    assert relation_queries
    assert all(" IN [1, 2]" in query for query in relation_queries)
    assert all("LIMIT 26" in query for query in relation_queries)
    assert all(", (" not in query for query in db.queries)
    assert all("a:Claim {id:" in query for query in relation_queries)
    assert all("ABOUT" not in query for query in relation_queries)


def test_relation_reads_raise_on_total_high_fanout():
    db = _HighFanoutRelationDB()
    with pytest.raises(ClaimReadLimitError, match="claim relation limit exceeded"):
        ClaimReader(db).read_relations(
            ClaimScope("product launch", "deadline", limit=2),
            {1, 2, 3, 4},
            "SUPERSEDES",
        )


def test_read_claims_fails_loudly_when_limit_is_exceeded():
    with pytest.raises(ClaimReadLimitError, match="claim scope limit exceeded"):
        ClaimReader(_LimitDB()).read_claims(
            ClaimScope("product launch", "deadline", limit=2)
        )


def test_chain_rejects_cross_subject_and_cross_predicate_adapter_rows():
    db = _CrossScopeChainDB()

    chain = ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))

    assert [row["id"] for row in chain] == [11]
    chain_queries = [query for query in db.queries if "RETURN older.id AS id" in query]
    assert chain_queries
    assert all(query.count("MATCH") == 3 for query in chain_queries)
    assert all(" IN [" not in query for query in db.queries)
    assert all(", (" not in query for query in db.queries)
    assert any("[:ABOUT]" in query and "{id: 11}" in query for query in db.queries)
    assert all("SUPERSEDES*" not in query for query in db.queries)


def test_chain_rejects_start_claim_outside_selected_scope():
    db = _CrossScopeChainDB(
        {"id": 10, "subject": "other project", "predicate": "deadline"}
    )

    chain = ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))

    assert chain == ()
    assert not any("SUPERSEDES*1..5" in query for query in db.queries)


def test_chain_stops_at_out_of_scope_intermediate_claim():
    db = _IterativeChainDB()

    chain = ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))

    assert {row["id"] for row in chain}.isdisjoint({11, 12})
    assert not any(
        "-[:SUPERSEDES]->" in query and "{id: 11}" in query for query in db.queries
    )
    assert all("SUPERSEDES*" not in query for query in db.queries)


def test_chain_is_cycle_safe():
    db = _CycleChainDB()
    with pytest.raises(GraphIntegrityError, match="supersession cycle"):
        ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))


def test_chain_accepts_shared_ancestor_in_branching_dag():
    db = _BranchingChainDB()

    chain = ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))

    assert [row["id"] for row in chain] == [11, 12, 13]


def test_chain_rejects_cycle_hidden_behind_a_shared_ancestor():
    db = _BranchingChainDB()
    db.edges[13] = (12,)

    with pytest.raises(GraphIntegrityError, match="supersession cycle"):
        ClaimReader(db).read_chain(10, ClaimScope("product launch", "deadline"))


def test_chain_reads_raise_on_high_fanout_one_hop():
    db = _CrossScopeChainDB()
    with pytest.raises(
        ClaimReadLimitError, match="claim chain relation limit exceeded"
    ):
        ClaimReader(db).read_chain(
            10, ClaimScope("product launch", "deadline", limit=2)
        )


def test_chain_depth_rejects_a_cycle_without_recursion():
    from hydraclaim.claim_read import _chain_depth

    with pytest.raises(GraphIntegrityError, match="supersession cycle"):
        _chain_depth([(1, 2), (2, 1)], {1, 2})


class _RelationLimitDB:
    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            return [{"id": 1, "subject": "product launch", "predicate": "deadline"}]
        if "SUPERSEDES" in cypher:
            return [
                {"new_id": 1, "old_id": 2},
                {"new_id": 1, "old_id": 3},
                {"new_id": 1, "old_id": 4},
            ]
        return []


def test_relation_reads_fail_on_high_fanout_after_limit_plus_one():
    with pytest.raises(ClaimReadLimitError, match="relation limit"):
        ClaimReader(_RelationLimitDB()).read_relations(
            ClaimScope("product launch", "deadline", limit=2), {1}, "SUPERSEDES"
        )


class _ChainLimitDB:
    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            return [{"id": 1, "subject": "product launch", "predicate": "deadline"}]
        if "SUPERSEDES" in cypher:
            return [
                {
                    "id": 2,
                    "value": "old-1",
                    "valid_from": "2026-08-02",
                    "valid_to": "",
                    "predicate": "deadline",
                },
                {
                    "id": 3,
                    "value": "old-2",
                    "valid_from": "2026-08-01",
                    "valid_to": "",
                    "predicate": "deadline",
                },
                {
                    "id": 4,
                    "value": "old-3",
                    "valid_from": "2026-07-31",
                    "valid_to": "",
                    "predicate": "deadline",
                },
            ]
        return []


def test_chain_reads_fail_on_high_fanout_after_limit_plus_one():
    with pytest.raises(ClaimReadLimitError, match="chain relation limit"):
        ClaimReader(_ChainLimitDB()).read_chain(
            1, ClaimScope("product launch", "deadline", limit=2)
        )
