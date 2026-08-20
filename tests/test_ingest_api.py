"""Offline tests for hydraclaim.ingest_api and serve.py write auth."""

from __future__ import annotations

import logging

from hydraclaim import ingest_api, serve


def test_write_auth_no_key_configured(monkeypatch):
    """When HYDRACLAIM_WRITE_KEY is empty, write endpoints are open."""
    monkeypatch.setattr(serve, "WRITE_KEY", "")
    assert serve._check_write_auth({}) is None
    assert serve._check_write_auth({"authorization": "Bearer whatever"}) is None


def test_write_auth_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "secret123")
    status, payload = serve._check_write_auth({})
    assert status == 401
    assert "write key" in payload["error"]


def test_write_auth_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "secret123")
    status, payload = serve._check_write_auth({"authorization": "Bearer wrong"})
    assert status == 401


def test_write_auth_accepts_correct_key(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "secret123")
    result = serve._check_write_auth({"authorization": "Bearer secret123"})
    assert result is None


def test_ingest_route_requires_auth(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "mykey")
    status, payload, extra = serve.dispatch(
        "POST", "/ingest", {"text": "hi"}, None, None, {}
    )
    assert status == 401


def test_ingest_route_passes_auth(monkeypatch):
    """With correct key, dispatch reaches the handler (which will fail on LLM, not auth)."""
    monkeypatch.setattr(serve, "WRITE_KEY", "mykey")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    status, payload, extra = serve.dispatch(
        "POST",
        "/ingest",
        {"text": "hello"},
        None,
        None,
        {"authorization": "Bearer mykey"},
    )
    assert status == 503
    assert "LLM_API_KEY" in payload["error"]


def test_ingest_slack_route_requires_auth(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "mykey")
    status, _, extra = serve.dispatch("POST", "/ingest/slack", {}, None, None, {})
    assert status == 401


def test_ingest_missing_text_returns_400(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "fake")
    status, payload, extra = serve.dispatch("POST", "/ingest", {}, None, None)
    assert status == 400
    assert "text" in payload["error"]


def test_ingest_invalid_source_kind(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "fake")
    status, payload, extra = serve.dispatch(
        "POST",
        "/ingest",
        {"text": "hi", "source_kind": "twitter"},
        None,
        None,
    )
    assert status == 400
    assert "source_kind" in payload["error"]


def test_ingest_failure_logs_step_and_traceback(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setattr("hydraclaim.ingest_api._discover_entities_llm", lambda text: [])

    def raising_extractor(session, entities, active):
        raise RuntimeError("extractor failed")

    monkeypatch.setattr(ingest_api, "extract_session", raising_extractor)
    request = {
        "text": "source text must not appear in logs",
        "source_kind": "slack",
        "author": "Ada",
        "channel": "general",
    }

    class EmptyDB:
        def query(self, cypher, consistency="causal"):
            return []

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest(request, EmptyDB())

    assert status == 500
    assert payload == {"code": "ingest_failed", "error": "ingestion failed"}
    assert "step=extract" in caplog.text
    assert "Traceback" in caplog.text
    assert request["text"] not in caplog.text
