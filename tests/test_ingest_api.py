"""Offline tests for hydraclaim.ingest_api and serve.py write auth."""

from __future__ import annotations

import logging

from hydraclaim import ingest_api, serve


class EmptyDB:
    def query(self, cypher, consistency="causal"):
        return []


def _message():
    return {
        "msg_id": "message-1",
        "ts": "2026-08-20T10:00:00+00:00",
        "author": "Asha Rao",
        "source_kind": "slack",
        "channel": "general",
        "text": "The project is active.",
    }


def _stub_event_store(monkeypatch):
    class Store:
        def __init__(self, db):
            pass

        def capture(self, event):
            return {"event_key": "event-1", "status": "CAPTURED", "created": True}

        def start_extraction(self, *args, **kwargs):
            return {"extraction_key": "extraction-1", "status": "RUNNING"}

        def fail_extraction(self, *args, **kwargs):
            return {"status": "FAILED"}

        def complete_extraction(self, *args, **kwargs):
            return {"status": "SUCCEEDED"}

    monkeypatch.setattr("hydraclaim.pipeline.SourceEventStore", Store)


def test_write_auth_no_key_configured(monkeypatch):
    """When HYDRACLAIM_WRITE_KEY is empty, write endpoints fail closed."""
    monkeypatch.setattr(serve, "WRITE_KEY", "")
    status, payload = serve._check_write_auth({})
    assert status == 503
    assert payload["code"] == "write_auth_not_configured"
    status, payload = serve._check_write_auth({"authorization": "Bearer whatever"})
    assert status == 503
    assert payload["code"] == "write_auth_not_configured"


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
    monkeypatch.setattr(serve, "WRITE_KEY", "local-write-key")
    monkeypatch.setenv("LLM_API_KEY", "fake")
    status, payload, extra = serve.dispatch(
        "POST", "/ingest", {}, None, None, {"authorization": "Bearer local-write-key"}
    )
    assert status == 400
    assert "text" in payload["error"]


def test_ingest_invalid_source_kind(monkeypatch):
    monkeypatch.setattr(serve, "WRITE_KEY", "local-write-key")
    monkeypatch.setenv("LLM_API_KEY", "fake")
    status, payload, extra = serve.dispatch(
        "POST",
        "/ingest",
        {"text": "hi", "source_kind": "twitter"},
        None,
        None,
        {"authorization": "Bearer local-write-key"},
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
    assert "scenario=adhoc-" in caplog.text
    assert "state=" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "input=" in caplog.text
    assert "Traceback" in caplog.text
    assert request["text"] not in caplog.text


def test_preformatted_failure_logs_read_active_state(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    _stub_event_store(monkeypatch)

    def raising_reader(db):
        raise RuntimeError("active read failed")

    monkeypatch.setattr("hydraclaim.pipeline.fetch_active_claims", raising_reader)
    request = {
        "scenario_id": "safe-scenario",
        "sessions": [{"session_id": "session-1", "messages": [_message()]}],
        "entities": [{"name": "Ada", "type": "person"}],
    }

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest(request, EmptyDB())

    assert status == 500
    assert payload == {"code": "ingest_failed", "error": "ingestion failed"}
    assert "step=read_active" in caplog.text
    assert "scenario=safe-scenario" in caplog.text
    assert "state=" in caplog.text
    assert "session_index=0" in caplog.text
    assert "session_count=1" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "Ada" not in caplog.text


def test_preformatted_failure_logs_extract_state(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    _stub_event_store(monkeypatch)
    monkeypatch.setattr("hydraclaim.pipeline.fetch_active_claims", lambda db: [])

    def raising_extractor(session, entities, active):
        raise RuntimeError("preformatted extraction failed")

    monkeypatch.setattr("hydraclaim.pipeline.extract_session", raising_extractor)
    request = {
        "scenario_id": "extract-scenario",
        "sessions": [{"session_id": "session-1", "messages": [_message()]}],
        "entities": [],
    }

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest(request, EmptyDB())

    assert status == 500
    assert payload["code"] == "ingest_failed"
    assert "step=extract" in caplog.text
    assert "active_count=0" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text


def test_preformatted_failure_logs_reconcile_state(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    _stub_event_store(monkeypatch)
    monkeypatch.setattr("hydraclaim.pipeline.fetch_active_claims", lambda db: [])
    monkeypatch.setattr(
        "hydraclaim.pipeline.extract_session",
        lambda session, entities, active: ([], []),
    )

    def raising_reconciler(*args, **kwargs):
        raise ValueError("reconcile failed")

    monkeypatch.setattr("hydraclaim.pipeline.plan_writes", raising_reconciler)
    request = {
        "scenario_id": "reconcile-scenario",
        "sessions": [{"session_id": "session-1", "messages": [_message()]}],
        "entities": [],
    }

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest(request, EmptyDB())

    assert status == 400
    assert payload["code"] == "invalid_request"
    assert "step=reconcile" in caplog.text
    assert "draft_count=0" in caplog.text
    assert "exception_type=ValueError" in caplog.text


def test_preformatted_failure_logs_graph_write_state(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    _stub_event_store(monkeypatch)
    monkeypatch.setattr("hydraclaim.pipeline.fetch_active_claims", lambda db: [])
    monkeypatch.setattr(
        "hydraclaim.pipeline.extract_session",
        lambda session, entities, active: ([], []),
    )
    monkeypatch.setattr(
        "hydraclaim.pipeline.plan_writes",
        lambda *args, **kwargs: {
            "create": [],
            "supersede": [],
            "contradict": [],
            "duplicates": 0,
            "warnings": [],
        },
    )

    class RaisingWriter:
        def __init__(self, db):
            pass

        def apply_plan(self, plan, scenario_id, entities, **kwargs):
            raise RuntimeError("graph write failed")

    monkeypatch.setattr("hydraclaim.pipeline.GraphWriter", RaisingWriter)
    request = {
        "scenario_id": "graph-scenario",
        "sessions": [{"session_id": "session-1", "messages": [_message()]}],
        "entities": [],
    }

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest(request, EmptyDB())

    assert status == 500
    assert payload["code"] == "ingest_failed"
    assert "step=graph_write" in caplog.text
    assert "plan_create_count=0" in caplog.text
    assert "applied=False" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text


def test_preformatted_validation_failure_logs_validation_step(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    request = {
        "scenario_id": "validation-scenario",
        "sessions": "not a list",
        "entities": [],
    }

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest(request, EmptyDB())

    assert status == 400
    assert payload["code"] == "invalid_request"
    assert "step=validation" in caplog.text
    assert "exception_type=ValueError" in caplog.text


def test_slack_dict_failure_logs_message_count(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setattr(ingest_api, "_discover_entities_llm", lambda text: [])

    def raising_extractor(session, entities, active):
        raise ValueError("Slack extraction failed")

    monkeypatch.setattr(ingest_api, "extract_session", raising_extractor)
    message = {
        "ts": "1716000000.000000",
        "user": "U123",
        "user_name": "Ada",
        "text": "private source text",
    }

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest_slack(
            {"channel": "general", "messages": [message]}, EmptyDB()
        )

    assert status == 400
    assert payload == {"code": "invalid_request", "error": "invalid ingestion input"}
    assert "step=extract" in caplog.text
    assert "message_count=1" in caplog.text
    assert "input_id=slack-dict" in caplog.text
    assert "private source text" not in caplog.text


def test_slack_bare_list_failure_logs_message_count_and_input_id(caplog, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setattr(ingest_api, "_discover_entities_llm", lambda text: [])

    def raising_extractor(session, entities, active):
        raise ValueError("Slack extraction failed")

    monkeypatch.setattr(ingest_api, "extract_session", raising_extractor)
    messages = [
        {
            "ts": "1716000000.000000",
            "user": "U123",
            "user_name": "Ada",
            "text": "bare list source text",
        }
    ]

    with caplog.at_level(logging.ERROR, logger="hydraclaim.ingest_api"):
        status, payload = ingest_api.handle_ingest_slack(messages, EmptyDB())

    assert status == 400
    assert payload == {"code": "invalid_request", "error": "invalid ingestion input"}
    assert "step=extract" in caplog.text
    assert "message_count=1" in caplog.text
    assert "input_id=slack-list" in caplog.text
    assert "bare list source text" not in caplog.text
