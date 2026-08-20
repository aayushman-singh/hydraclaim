from __future__ import annotations

from datetime import datetime, timezone

from hydraclaim.claim_read import ClaimReader, ClaimScope


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


class _FakeDB:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.queries.append(cypher)
        if "MATCH (e:Entity)" in cypher:
            return [{"name": "product launch", "aliases": "launch"}]
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
        return [
            {"new_id": 1, "old_id": 2},
            {"new_id": 1, "old_id": 99},
        ]


def test_answer_returns_structured_abstention():
    result = ClaimReader(_FakeDB()).answer("Who owns unknown?", now=NOW)

    assert result.route == "ABSTAIN"
    assert result.citations == ()
    assert result.classification is not None


def test_probe_queries_limit_relations_to_selected_claims():
    db = _FakeDB()
    ClaimReader(db).answer("Who owns launch?", now=NOW)

    relation_queries = [
        query for query in db.queries if "SUPERSEDES" in query or "CONTRADICTS" in query
    ]
    assert relation_queries
    assert all("ABOUT" in query and "e.name" in query for query in relation_queries)


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
    assert result.citations[0].claim_id == "claim-1"


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
    query = next(query for query in db.queries if "SUPERSEDES*1..5" in query)
    assert "c.id = 10" in query
    assert "start_e.name" in query
    assert "start_e.name" in query and "c.predicate" in query
    assert "older.predicate" in query
    assert "ORDER BY older.valid_from DESC, older.id DESC" in query


def test_relation_reads_filter_unselected_endpoints():
    db = _RelationDB()

    relations = ClaimReader(db).read_relations(
        ClaimScope("product launch", "deadline"), {1, 2}, "SUPERSEDES"
    )

    assert relations == ({"new_id": 1, "old_id": 2},)
