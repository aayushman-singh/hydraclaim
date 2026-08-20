from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from hydraclaim import cli, config, pipeline
from hydraclaim.extract import parse_claims
from hydraclaim.errors import PipelineInputError


class _RecordingDB:
    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[str] = []

    def query(self, cypher: str, consistency: str = "causal") -> list[dict]:
        self.reads.append(cypher)
        if cypher.lstrip().startswith(("CREATE", "SET")):
            self.writes.append(cypher)
        return []


def _document() -> dict:
    return {
        "scenario_id": "strict-extraction",
        "entities": [],
        "sessions": [
            {
                "session_id": "s1",
                "messages": [
                    {
                        "msg_id": "s1-m1",
                        "ts": "2026-08-01T00:00:00+00:00",
                        "author": "Asha Rao",
                        "source_kind": "slack",
                        "channel": "general",
                        "text": "The project is active.",
                    }
                ],
            }
        ],
    }


def _valid_claim() -> dict:
    return {
        "subject": "project",
        "predicate": "status",
        "value": "active",
        "valid_from": "2026-08-01",
        "quote": "The project is active.",
        "author": "Asha Rao",
        "source_kind": "slack",
        "session_id": "s1",
        "msg_id": "s1-m1",
        "explicitness": 1.0,
        "confidence": 1.0,
        "supersedes": None,
    }


@pytest.mark.parametrize(
    "response",
    [
        {"wrong": []},
        {"claims": [_valid_claim() | {"unknown": True}]},
        {"claims": [_valid_claim() | {"value": 7}]},
    ],
)
def test_malformed_extraction_preserves_capture_and_records_failure(
    monkeypatch, response
):
    db = _RecordingDB()
    actions = []

    class Store:
        def __init__(self, _db):
            pass

        def capture(self, event):
            actions.append(("capture", event["source_id"]))
            return {"event_key": "event-1", "status": "CAPTURED", "created": True}

        def start_extraction(self, *args, **kwargs):
            actions.append(("start", args[0]))
            return {"extraction_key": "extraction-1", "status": "RUNNING"}

        def fail_extraction(self, _key, step, _exc):
            actions.append(("fail", step))

    def strict_extract(session, entities, active):
        return parse_claims(response, session, active)

    monkeypatch.setattr(pipeline, "extract_session", strict_extract)
    monkeypatch.setattr(pipeline, "SourceEventStore", Store)
    with pytest.raises(ValueError):
        pipeline.run_pipeline(db, _document())

    assert actions == [("capture", "s1-m1"), ("start", "event-1"), ("fail", "EXTRACT")]


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {},
        {"scenario_id": "scenario", "entities": "wrong", "sessions": []},
        {"scenario_id": "scenario", "entities": [{}], "sessions": []},
        {"scenario_id": "scenario", "entities": [], "sessions": ["wrong"]},
        {
            "scenario_id": "scenario",
            "entities": [],
            "sessions": [{"session_id": "s1", "messages": "wrong"}],
        },
        {"scenario_id": "scenario", "entities": [], "sessions": [{}]},
        {
            "scenario_id": "scenario",
            "entities": [],
            "sessions": [{"session_id": "s1", "messages": [{}]}],
        },
    ],
)
def test_pipeline_rejects_malformed_document_before_db_access(document):
    db = _RecordingDB()

    with pytest.raises(PipelineInputError, match="invalid pipeline document"):
        pipeline.run_pipeline(db, document)

    assert db.reads == []
    assert db.writes == []


@pytest.mark.parametrize("timestamp", ["not-a-timestamp", "12345"])
def test_pipeline_rejects_invalid_message_timestamp_before_db_access(timestamp):
    db = _RecordingDB()
    document = _document()
    document["sessions"][0]["messages"][0]["ts"] = timestamp

    with pytest.raises(PipelineInputError, match="timestamp"):
        pipeline.run_pipeline(db, document)

    assert db.reads == []
    assert db.writes == []


def test_unified_cli_reports_malformed_pipeline_document_without_connecting(
    monkeypatch, tmp_path, capsys
):
    scenario_path = tmp_path / "malformed.json"
    scenario_path.write_text(json.dumps({"scenario_id": "missing-fields"}))
    monkeypatch.setattr(config, "require_settings", lambda **kwargs: None)
    monkeypatch.setattr(
        config,
        "connect",
        lambda: pytest.fail("pipeline connected before validating its document"),
    )

    assert cli.main(["pipeline", str(scenario_path)]) == 1

    stderr = capsys.readouterr().err
    assert "hydraclaim: error: validation_error:" in stderr
    assert "invalid pipeline document" in stderr
    assert "Traceback" not in stderr


def test_pipeline_module_reports_expected_failure_without_traceback(tmp_path):
    scenario_path = tmp_path / "malformed.json"
    scenario_path.write_text(json.dumps({"scenario_id": "missing-fields"}))
    environment = os.environ.copy()
    environment["LLM_API_KEY"] = "test-key"

    completed = subprocess.run(
        [sys.executable, "-m", "hydraclaim.pipeline", str(scenario_path)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 1
    assert "hydraclaim: error: validation_error:" in completed.stderr
    assert "invalid pipeline document" in completed.stderr
    assert "Traceback" not in completed.stderr
