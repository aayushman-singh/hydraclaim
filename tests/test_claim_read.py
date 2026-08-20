from __future__ import annotations

from datetime import datetime, timezone

from hydraclaim.claim_read import ClaimReader


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
