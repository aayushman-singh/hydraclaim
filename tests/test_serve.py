"""Offline tests for hydraclaim.serve — no HydraDB, no network."""

from __future__ import annotations

import json

import pytest

from hydraclaim import retrieve, serve
from hydraclaim.llm import LLMError


class FakeDB:
    def __init__(self, rows_by_substring=None):
        self.rows_by_substring = rows_by_substring or {}
        self.queries = []

    def query(self, cypher, consistency="causal"):
        self.queries.append(cypher)
        for needle, rows in self.rows_by_substring.items():
            if needle in cypher:
                return rows
        return []


def fake_answer(db, question, **kwargs):
    return {
        "route": "FAST",
        "answer": f"stub answer for: {question}",
        "citations": [
            {"claim_id": "c1", "source_kind": "meeting", "author": "A", "quote": "q"}
        ],
        "classification": {"subject": "product launch", "predicate": "deadline"},
        "probe": {"coverage": 1, "conflicts": 0},
    }


@pytest.fixture(autouse=True)
def _stub_answer(monkeypatch):
    monkeypatch.setattr(retrieve, "answer", fake_answer)


def test_dispatch_health():
    status, payload, _ = serve.dispatch("GET", "/health", {}, None, None)
    assert status == 200
    assert payload == {"status": "ok"}


def test_dispatch_unknown_endpoint():
    status, payload, _ = serve.dispatch("GET", "/nope", {}, None, None)
    assert status == 404
    assert payload["code"] == "not_found"
    assert "unknown endpoint" in payload["error"]


def test_dispatch_invalid_request_has_stable_error_code():
    status, payload, _ = serve.dispatch("POST", "/ask", {}, None, None)
    assert status == 400
    assert payload == {
        "code": "invalid_request",
        "error": "missing 'question' in request body",
    }


def test_dispatch_ingest_route_returns_stable_failure(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "")
    monkeypatch.setattr(
        "hydraclaim.ingest_api.handle_ingest",
        lambda body, db: (500, {"code": "ingest_failed", "error": "ingestion failed"}),
    )

    status, payload, extra = serve.dispatch(
        "POST", "/ingest", {"text": "safe"}, FakeDB(), None
    )

    assert status == 500
    assert payload == {"code": "ingest_failed", "error": "ingestion failed"}
    assert extra is None


def test_dispatch_slack_ingest_route_returns_stable_failure(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "")
    monkeypatch.setattr(
        "hydraclaim.ingest_api.handle_ingest_slack",
        lambda body, db: (500, {"code": "ingest_failed", "error": "ingestion failed"}),
    )

    status, payload, extra = serve.dispatch("POST", "/ingest/slack", [], FakeDB(), None)

    assert status == 500
    assert payload == {"code": "ingest_failed", "error": "ingestion failed"}
    assert extra is None


def test_dispatch_ask_requires_question():
    status, payload, _ = serve.dispatch("POST", "/ask", {}, None, None)
    assert status == 400
    status, payload, _ = serve.dispatch("POST", "/ask", {"question": "  "}, None, None)
    assert status == 400


def test_dispatch_ask_returns_answer():
    status, payload, _ = serve.dispatch(
        "POST",
        "/ask",
        {"question": "What is the current launch deadline?"},
        FakeDB(),
        None,
    )
    assert status == 200
    assert payload["route"] == "FAST"
    assert payload["citations"][0]["claim_id"] == "c1"


def test_llm_classifier_propagates_error(monkeypatch):

    def boom(**kwargs):
        from hydraclaim.llm import LLMError

        raise LLMError("deepseek unreachable")

    monkeypatch.setattr(
        "hydraclaim.router.llm_classifier",
        lambda q: boom(),
    )
    with pytest.raises(LLMError):
        serve.llm_classifier("Who owns the payments integration?")


def test_handle_ask_passes_classification_mode(monkeypatch):
    seen = {}

    def capture(db, question, **kwargs):
        seen.update(kwargs)
        return {
            "route": "ABSTAIN",
            "answer": "",
            "citations": [],
            "classification": {},
            "probe": None,
        }

    monkeypatch.setattr(retrieve, "answer", capture)
    serve.handle_ask("Who owns payments?", FakeDB(), lambda _: {}, "llm")
    assert seen["classification_mode"] == "llm"


def test_http_ask_passes_llm_classification_mode_to_retrieval(monkeypatch):
    seen = {}

    def capture(db, question, **kwargs):
        seen.update(kwargs)
        return {
            "route": "ABSTAIN",
            "answer": "",
            "citations": [],
            "classification": {},
            "probe": None,
        }

    monkeypatch.setattr(retrieve, "answer", capture)
    status, _, _ = serve.dispatch(
        "POST",
        "/ask",
        {"question": "Who owns payments?"},
        FakeDB(),
        lambda _: {},
        classification_mode="llm",
    )

    assert status == 200
    assert seen["classification_mode"] == "llm"


def test_handle_scenarios_reads_generated_data():
    payload = serve.handle_scenarios()
    ids = {s["id"] for s in payload["scenarios"]}
    assert "deadline_drift" in ids
    assert all(s["questions"] for s in payload["scenarios"])


def test_handle_graph_shapes_nodes_and_edges():
    db = FakeDB(
        {
            "MATCH (e:Entity)": [
                {"id": 1, "name": "product launch", "type": "project"}
            ],
            "[:ABOUT]->(e:Entity)": [
                {
                    "id": 10,
                    "key": "scen:c1",
                    "subject": "product launch",
                    "predicate": "deadline",
                    "value": "2026-10-17",
                    "status": "active",
                    "valid_from": "2026-05-18",
                    "valid_to": "",
                },
                {
                    "id": 11,
                    "key": "scen:c0",
                    "subject": "product launch",
                    "predicate": "deadline",
                    "value": "2026-10-10",
                    "status": "superseded",
                    "valid_from": "2026-05-10",
                    "valid_to": "2026-05-18",
                },
            ],
            "[:SUPERSEDES]->": [{"src": 10, "dst": 11}],
            "[:CONTRADICTS]->": [],
        }
    )
    payload = serve.handle_graph(db)
    nodes = {(n["id"], n["kind"]) for n in payload["nodes"]}
    assert (1, "entity") in nodes and (10, "claim") in nodes
    edge_types = {(e["from"], e["to"], e["type"]) for e in payload["edges"]}
    assert (10, 11, "SUPERSEDES") in edge_types
    assert (10, 1, "ABOUT") in edge_types  # claim linked to its entity


def test_handler_endpoints_over_http():
    """Smoke-test the HTTP layer with the real handler against a stub server."""
    import http.client
    import threading

    server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.DemoHandler)
    server.db = FakeDB()
    server.llm_fn = None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read()) == {"status": "ok"}

        conn.request(
            "POST",
            "/ask",
            body=json.dumps({"question": "test?"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        assert "stub answer" in json.loads(resp.read())["answer"]

        conn.request(
            "POST",
            "/ask",
            body=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 400
    finally:
        server.shutdown()
        server.server_close()
