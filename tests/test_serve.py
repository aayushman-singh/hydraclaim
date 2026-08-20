"""Offline tests for hydraclaim.serve — no HydraDB, no network."""

from __future__ import annotations

import json

import pytest

from hydraclaim import retrieve, serve
from hydraclaim.claim_read import ClaimReadLimitError
from hydraclaim.errors import GraphIntegrityError
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


@pytest.mark.parametrize("body", [[], None])
def test_dispatch_rejects_non_object_ask_body(body):
    status, payload, _ = serve.dispatch("POST", "/ask", body, FakeDB(), None)

    assert status == 400
    assert payload == {
        "code": "invalid_request",
        "error": "request body must be a JSON object",
    }


def test_dispatch_ingest_route_returns_stable_failure(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "local-write-key")
    monkeypatch.setattr(
        "hydraclaim.ingest_api.handle_ingest",
        lambda body, db: (500, {"code": "ingest_failed", "error": "ingestion failed"}),
    )

    status, payload, extra = serve.dispatch(
        "POST",
        "/ingest",
        {"text": "safe"},
        FakeDB(),
        None,
        {"authorization": "Bearer local-write-key"},
    )

    assert status == 500
    assert payload == {"code": "ingest_failed", "error": "ingestion failed"}
    assert extra is None


def test_dispatch_slack_ingest_route_returns_stable_failure(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "local-write-key")
    monkeypatch.setattr(
        "hydraclaim.ingest_api.handle_ingest_slack",
        lambda body, db: (500, {"code": "ingest_failed", "error": "ingestion failed"}),
    )

    status, payload, extra = serve.dispatch(
        "POST",
        "/ingest/slack",
        [],
        FakeDB(),
        None,
        {"authorization": "Bearer local-write-key"},
    )

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


def test_dispatch_maps_llm_failure_and_logs_traceback(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise LLMError("provider unavailable")

    monkeypatch.setattr(retrieve, "answer", broken)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "POST", "/ask", {"question": "Who owns payments?"}, FakeDB(), None
        )

    assert status == 502
    assert payload == {
        "code": "llm_failed",
        "error": "language model request failed",
    }
    assert "endpoint=/ask" in caplog.text
    assert "Traceback" in caplog.text


def test_dispatch_maps_classifier_shape_failure(monkeypatch, caplog):
    from hydraclaim.router import ClassificationError

    def broken(*args, **kwargs):
        raise ClassificationError("classifier returned an array")

    monkeypatch.setattr(retrieve, "answer", broken)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "POST", "/ask", {"question": "Who owns payments?"}, FakeDB(), None
        )

    assert status == 502
    assert payload == {
        "code": "classifier_failed",
        "error": "question classification failed",
    }
    assert "endpoint=/ask" in caplog.text
    assert "Traceback" in caplog.text


def test_dispatch_maps_claim_limit_for_ask_and_logs_safe_context(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise ClaimReadLimitError(
            "claim scope limit exceeded", subject="payments", limit=3
        )

    monkeypatch.setattr(serve, "handle_ask", broken)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "POST",
            "/ask",
            {"question": "What is the private source text?"},
            FakeDB(),
            None,
        )

    assert status == 409
    assert payload == {
        "code": "claim_limit_exceeded",
        "error": "claim read limit exceeded",
    }
    assert "endpoint=/ask" in caplog.text
    assert "question_length=32" in caplog.text
    assert "limit=3" in caplog.text
    assert "exception_type=ClaimReadLimitError" in caplog.text
    assert "Traceback" in caplog.text
    assert "private source text" not in caplog.text


def test_dispatch_maps_bare_claim_limit_for_ask(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise ClaimReadLimitError()

    monkeypatch.setattr(serve, "handle_ask", broken)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "POST", "/ask", {"question": "Who owns payments?"}, FakeDB(), None
        )

    assert status == 409
    assert payload == {
        "code": "claim_limit_exceeded",
        "error": "claim read limit exceeded",
    }
    assert "endpoint=/ask" in caplog.text
    assert "limit=25" in caplog.text
    assert "exception_type=ClaimReadLimitError" in caplog.text
    assert "Traceback" in caplog.text


def test_dispatch_maps_graph_integrity_for_ask(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise GraphIntegrityError("supersession cycle")

    monkeypatch.setattr(serve, "handle_ask", broken)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "POST", "/ask", {"question": "Who owns payments?"}, FakeDB(), None
        )

    assert status == 409
    assert payload == {
        "code": "graph_integrity_error",
        "error": "graph integrity error",
    }
    assert "exception_type=GraphIntegrityError" in caplog.text
    assert "Traceback" in caplog.text


def test_dispatch_maps_claim_limit_for_graph_and_logs_subject(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise ClaimReadLimitError(
            "claim scope limit exceeded", subject="product launch", limit=2
        )

    monkeypatch.setattr(serve, "handle_graph", broken)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch("GET", "/graph", {}, FakeDB(), None)

    assert status == 409
    assert payload == {
        "code": "claim_limit_exceeded",
        "error": "claim read limit exceeded",
    }
    assert "endpoint=/graph" in caplog.text
    assert "subject='product launch'" in caplog.text
    assert "limit=2" in caplog.text
    assert "exception_type=ClaimReadLimitError" in caplog.text
    assert "Traceback" in caplog.text


@pytest.mark.parametrize("classifier_response", [[], None])
def test_dispatch_maps_array_or_null_classifier_response(
    classifier_response, caplog, monkeypatch
):
    def run_reader(question, db, llm_fn, classification_mode):
        return (
            serve.ClaimReader(db)
            .answer(question, classification_mode=classification_mode, llm_fn=llm_fn)
            .as_dict()
        )

    monkeypatch.setattr(serve, "handle_ask", run_reader)
    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "POST",
            "/ask",
            {"question": "Who owns payments?"},
            FakeDB(),
            lambda question: classifier_response,
            classification_mode="llm",
        )

    assert status == 502
    assert payload == {
        "code": "classifier_failed",
        "error": "question classification failed",
    }
    assert "Traceback" in caplog.text


def test_dispatch_does_not_hide_unrecognized_programming_errors(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("programming error")

    monkeypatch.setattr(retrieve, "answer", broken)
    with pytest.raises(ValueError, match="programming error"):
        serve.dispatch(
            "POST", "/ask", {"question": "Who owns payments?"}, FakeDB(), None
        )


@pytest.mark.parametrize("response", [{"suggestions": []}, None])
def test_llm_suggestions_fail_without_heuristic_fallback(monkeypatch, response):
    monkeypatch.setattr("hydraclaim.llm.chat_json", lambda messages: response)

    if response is None:
        with pytest.raises(serve.SuggestionResponseError, match="suggestion response"):
            serve.handle_suggestions(object(), suggestion_mode="llm")
    else:
        assert serve.handle_suggestions(object(), suggestion_mode="llm") == {
            "suggestions": []
        }


def test_llm_suggestions_return_empty_without_heuristic_baseline(monkeypatch):
    monkeypatch.setattr(
        serve, "DATA_DIR", type("EmptyDir", (), {"glob": lambda *_: []})()
    )
    monkeypatch.setattr(
        "hydraclaim.llm.chat_json", lambda messages: {"suggestions": []}
    )

    assert serve.handle_suggestions(object(), suggestion_mode="llm") == {
        "suggestions": []
    }


def test_llm_suggestions_reject_duplicate_routes(monkeypatch):
    monkeypatch.setattr(
        "hydraclaim.llm.chat_json",
        lambda messages: {
            "suggestions": [
                {"text": "first", "route": "FAST"},
                {"text": "second", "route": "FAST"},
            ]
        },
    )

    with pytest.raises(serve.SuggestionResponseError, match="duplicate route"):
        serve.handle_suggestions(object(), suggestion_mode="llm")


@pytest.mark.parametrize(
    ("raw", "status", "code"),
    [
        (None, 411, "content_length_required"),
        ("abc", 400, "invalid_content_length"),
        ("-1", 400, "invalid_content_length"),
        ("500001", 413, "request_too_large"),
    ],
)
def test_content_length_validation_is_bounded(raw, status, code):
    result = serve.validate_content_length(raw)
    assert result[0] == status
    assert result[1]["code"] == code


@pytest.mark.parametrize(
    "raw, status",
    [(None, 411), ("abc", 400), ("-1", 400), ("500001", 413)],
)
def test_handler_rejects_invalid_content_length_before_read(raw, status):
    class ReadTracker:
        def __init__(self):
            self.calls = []

        def read(self, length):
            self.calls.append(length)
            raise AssertionError("handler read an invalid request body")

    class Server:
        db = FakeDB()
        llm_fn = None
        classification_mode = "heuristic"
        suggestion_mode = "heuristic"

    handler = object.__new__(serve.DemoHandler)
    handler.command = "POST"
    handler.path = "/ask"
    handler.headers = {} if raw is None else {"Content-Length": raw}
    handler.rfile = ReadTracker()
    handler.server = Server()
    handler.client_address = ("127.0.0.1", 1234)

    result = handler._dispatch()

    assert result[0] == status
    assert handler.rfile.calls == []


def test_dispatch_maps_llm_suggestion_failure(monkeypatch, caplog):
    monkeypatch.setattr(
        serve,
        "handle_suggestions",
        lambda *args, **kwargs: (_ for _ in ()).throw(LLMError("offline")),
    )

    with caplog.at_level("ERROR", logger="hydraclaim.serve"):
        status, payload, _ = serve.dispatch(
            "GET", "/suggestions", {}, FakeDB(), object(), suggestion_mode="llm"
        )

    assert status == 502
    assert payload == {
        "code": "llm_failed",
        "error": "language model request failed",
    }
    assert "endpoint=/suggestions" in caplog.text
    assert "Traceback" in caplog.text


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


def test_handle_scenarios_reads_generated_data(tmp_path, monkeypatch):
    scenario = {
        "scenario_id": "deadline_drift",
        "description": "The deadline changes.",
        "ground_truth": {"qa": [{"question": "When is the deadline?"}]},
    }
    (tmp_path / "deadline_drift.json").write_text(
        json.dumps(scenario), encoding="utf-8"
    )
    monkeypatch.setattr(serve, "DATA_DIR", tmp_path)

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
            "[:ABOUT]->(e:Entity {name:": [
                {"id": 10, "subject": "product launch", "predicate": "deadline"},
                {"id": 11, "subject": "product launch", "predicate": "deadline"},
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
