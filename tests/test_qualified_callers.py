from __future__ import annotations

from contextlib import contextmanager
import sys
from types import SimpleNamespace
from types import ModuleType

import pytest

from hydraclaim import benchmark, probe, retrieve, serve
from hydraclaim.claims import QualifierSelection


def test_retrieve_answer_forwards_exact_qualifier(monkeypatch):
    received = {}

    class Reader:
        def __init__(self, db):
            received["db"] = db

        def answer(self, question, **kwargs):
            received.update(question=question, **kwargs)
            return SimpleNamespace(as_dict=lambda: {"answer": "supported"})

    monkeypatch.setattr(retrieve, "ClaimReader", Reader)
    db = object()
    result = retrieve.answer(db, "What was decided?", qualifier="cart access")

    assert result == {"answer": "supported"}
    assert received["db"] is db
    assert received["qualifier"] == "cart access"


def test_fetch_claims_passes_qualifier_to_scope(monkeypatch):
    received = {}

    class Reader:
        def __init__(self, db):
            pass

        def read_claims(self, scope):
            received["scope"] = scope
            return ()

    monkeypatch.setattr(retrieve, "ClaimReader", Reader)
    retrieve.fetch_claims(
        object(), "owner", "decided", qualifier="cart access", active_only=True
    )

    scope = received["scope"]
    assert scope.subject == "owner"
    assert scope.predicate == "decided"
    assert scope.qualifier == "cart access"
    assert scope.active_only is True


def test_probe_passes_explicit_all_selection(monkeypatch):
    received = {}

    class Reader:
        def __init__(self, db):
            pass

        def probe(self, scope):
            received["scope"] = scope
            return "probe"

    monkeypatch.setattr(probe, "ClaimReader", Reader)
    assert (
        probe.probe(object(), "owner", "decided", qualifier=QualifierSelection.ALL)
        == "probe"
    )
    assert received["scope"].qualifier is QualifierSelection.ALL


@pytest.mark.parametrize(
    ("body_fields", "expected"),
    [
        ({"qualifier": "cart access"}, "cart access"),
        ({"all_qualifiers": True}, QualifierSelection.ALL),
    ],
)
def test_http_ask_passes_explicit_selection(monkeypatch, body_fields, expected):
    received = {}
    monkeypatch.setattr(serve.limiter, "hit", lambda *args: (True, 0))

    def handle(question, db, llm_fn, mode, *, qualifier=None):
        received.update(question=question, qualifier=qualifier)
        return {"answer": "supported"}

    monkeypatch.setattr(serve, "handle_ask", handle)
    status, payload, _ = serve.dispatch(
        "POST",
        "/ask",
        {"question": "What was decided?", **body_fields},
        object(),
        None,
    )

    assert status == 200
    assert payload == {"answer": "supported"}
    assert received["qualifier"] == expected


def test_http_ask_rejects_exact_and_all_selection_together():
    status, payload, _ = serve.dispatch(
        "POST",
        "/ask",
        {
            "question": "What was decided?",
            "qualifier": "cart access",
            "all_qualifiers": True,
        },
        object(),
        None,
    )

    assert status == 400
    assert "cannot be used together" in payload["error"]


def test_graph_metadata_listing_selects_all_qualifiers(monkeypatch):
    scopes = []

    class Reader:
        def __init__(self, db):
            pass

        def read_entities(self):
            return ({"name": "owner", "id": 1, "aliases": [], "type": "person"},)

        def read_claims(self, scope):
            scopes.append(scope)
            return ()

        def read_relations(self, scope, claim_ids, relation_type):
            return ()

    monkeypatch.setattr(serve, "ClaimReader", Reader)
    assert serve.handle_graph(object()) == {
        "nodes": [{"id": 1, "label": "owner", "kind": "entity", "type": "person"}],
        "edges": [],
    }
    assert scopes[0].qualifier is QualifierSelection.ALL


def test_benchmark_metadata_read_selects_all_qualifiers(monkeypatch):
    scopes = []

    class Reader:
        def __init__(self, db):
            pass

        def read_claims(self, scope):
            scopes.append(scope)
            return ()

    monkeypatch.setattr(benchmark, "ClaimReader", Reader)
    benchmark.naive_answer(
        object(), "What did the owner discuss?", [{"name": "owner", "aliases": []}]
    )
    assert scopes[0].qualifier is QualifierSelection.ALL


def test_cli_exact_and_all_options_are_mutually_exclusive(monkeypatch):
    from hydraclaim import ask

    with pytest.raises(SystemExit):
        ask.main(["question", "--qualifier", "cart access", "--all-qualifiers"])


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (["--qualifier", "cart access"], "cart access"),
        (["--all-qualifiers"], QualifierSelection.ALL),
    ],
)
def test_cli_passes_explicit_qualifier(monkeypatch, options, expected):
    from hydraclaim import ask, config

    captured = {}

    @contextmanager
    def connect():
        yield object()

    def answer(db, question, **kwargs):
        captured.update(question=question, **kwargs)
        return {
            "route": "ABSTAIN",
            "classification": {},
            "probe": None,
            "answer": "abstain",
            "citations": [],
        }

    monkeypatch.setattr(config, "connect", connect)
    monkeypatch.setattr(config, "require_settings", lambda **kwargs: None)
    monkeypatch.setattr(retrieve, "answer", answer)
    ask.main(["What was decided?", *options])
    assert captured["qualifier"] == expected


def test_mcp_exposes_exact_and_all_selection(monkeypatch):
    received = []
    db = object()

    class FakeServer:
        def __init__(self, *args, **kwargs):
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    mcp_server_module = ModuleType("mcp.server")
    mcp_server_module.MCPServer = FakeServer
    monkeypatch.setitem(sys.modules, "mcp.server", mcp_server_module)

    from hydraclaim import mcp_server

    monkeypatch.setattr(mcp_server, "MCPServer", FakeServer)

    @contextmanager
    def connect():
        yield db

    def answer(database, question, **kwargs):
        assert database is db
        received.append(kwargs["qualifier"])
        return {"answer": "supported"}

    server = mcp_server.create_server(connect_fn=connect, answer_fn=answer)
    exact = server.tools["ask_claim"]("What was decided?", qualifier="cart access")
    all_scopes = server.tools["ask_claim"]("What was decided?", all_qualifiers=True)
    assert exact == all_scopes == {"answer": "supported"}
    with pytest.raises(ValueError, match="cannot be used together"):
        server.tools["ask_claim"](
            "What was decided?", qualifier="cart access", all_qualifiers=True
        )
    assert received == ["cart access", QualifierSelection.ALL]
