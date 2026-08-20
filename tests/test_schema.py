from pathlib import Path

from hydraclaim.db import HydraDBError
from hydraclaim import schema
from hydraclaim.schema import _probes, verify


def test_schema_document_uses_verified_dialect():
    text = Path("hydraclaim/schema.cypher").read_text(encoding="utf-8")
    assert "TGProbe" not in text
    assert "IS NULL" not in text
    assert "length(" not in text
    assert "-[:CONTRADICTS]-" not in text


def test_schema_document_matches_verified_reference():
    text = Path("hydraclaim/schema.cypher").read_text(encoding="utf-8")
    render = getattr(schema, "render_schema_reference", None)
    assert render is not None
    assert text == render()


def test_provenance_query_matches_claim_key():
    text = Path("hydraclaim/schema.cypher").read_text(encoding="utf-8")
    assert "MATCH (c:Claim {key: 'deadline_drift:dl-3'})" in text
    assert "MATCH (c:Claim {id: 'deadline_drift:dl-3'})" not in text


def test_probe_uses_hydraclaim_label():
    statements = [query for _, queries in _probes("00000001") for query in queries]
    assert all("TGProbe" not in query for query in statements)
    assert any("HydraClaimProbe" in query for query in statements)


class _CleanupFailureDB:
    def query(self, cypher):
        if "{run:" in cypher and "DETACH DELETE" in cypher:
            raise HydraDBError("cleanup failed")
        return []


def test_cleanup_failure_fails_verification():
    assert verify(_CleanupFailureDB()) is False
