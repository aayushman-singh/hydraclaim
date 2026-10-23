from __future__ import annotations

import pytest

from hydraclaim import process
from hydraclaim.extract import extract_session


def _event():
    return {
        "event": {
            "occurred_at": "2026-08-20T10:30:00+00:00",
            "author": "Asha Rao",
            "source_kind": "slack",
            "content": "Launch moves to Monday.",
        }
    }


class _Store:
    actions = []

    def __init__(self, _db):
        self.actions = []
        type(self).actions = self.actions

    def start_extraction(
        self, event_key, provider, model, prompt_version, *, reprocess
    ):
        self.actions.append(("start", event_key, provider, model, prompt_version))
        return {"extraction_key": "event:extraction:1", "status": "RUNNING"}

    def fail_extraction(self, extraction_key, step, exc):
        self.actions.append(("fail", extraction_key, step, exc))
        return {"status": "FAILED", "step": step}

    def complete_extraction(self, extraction_key, claim_keys):
        self.actions.append(("complete", extraction_key, claim_keys))
        return {"extraction_key": extraction_key, "status": "SUCCEEDED"}


class _Writer:
    def __init__(self, _db):
        pass

    def apply_plan(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _patch_process_dependencies(monkeypatch):
    _Store.actions = []
    monkeypatch.setattr(process, "read_event", lambda _db, _key: _event())
    monkeypatch.setattr(process, "SourceEventStore", _Store)
    monkeypatch.setattr(process, "fetch_active_claims", lambda _db: [])
    monkeypatch.setattr(
        process, "plan_writes", lambda *_args, **_kwargs: {"create": []}
    )
    monkeypatch.setattr(process, "GraphWriter", _Writer)


def test_process_event_uses_required_extractor_and_records_explicit_metadata():
    seen = []

    def extractor(session, entities, active):
        seen.extend((session, entities, active))
        return [], []

    result = process.process_event(
        object(),
        "event",
        extractor=extractor,
        provider="jarvis-codex",
        model="codex-test",
    )

    assert result == {
        "extraction_key": "event:extraction:1",
        "status": "SUCCEEDED",
    }
    assert seen[0]["messages"][0]["text"] == "Launch moves to Monday."
    assert seen[1:] == [[], []]
    assert _Store.actions == [
        ("start", "event", "jarvis-codex", "codex-test", "extract-v1"),
        ("complete", "event:extraction:1", []),
    ]


def test_process_event_records_extractor_failure_and_stops():
    failure = RuntimeError("extractor unavailable")

    def extractor(_session, _entities, _active):
        raise failure

    with pytest.raises(RuntimeError, match="extractor unavailable"):
        process.process_event(
            object(),
            "event",
            extractor=extractor,
            provider="jarvis-codex",
            model="codex-test",
        )

    assert len(_Store.actions) == 2
    assert _Store.actions[0] == (
        "start",
        "event",
        "jarvis-codex",
        "codex-test",
        "extract-v1",
    )
    assert _Store.actions[1][:3] == (
        "fail",
        "event:extraction:1",
        "EXTRACT",
    )
    assert _Store.actions[1][3] is failure


def test_process_cli_passes_configured_extractor_provider_and_model(
    monkeypatch, capsys
):
    from contextlib import nullcontext

    from hydraclaim import config

    seen = {}

    def run(_db, event_key, **kwargs):
        seen.update(event_key=event_key, **kwargs)
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(config, "require_settings", lambda **_kwargs: None)
    monkeypatch.setattr(config, "connect", lambda: nullcontext(object()))
    monkeypatch.setattr(process, "process_event", run)
    monkeypatch.setenv("LLM_MODEL", "cli-model")

    assert process.main(["event", "--reprocess"]) == 0

    assert seen == {
        "event_key": "event",
        "extractor": extract_session,
        "provider": "openai-compatible",
        "model": "cli-model",
        "reprocess": True,
    }
    assert capsys.readouterr().out.strip() == '{"status": "SUCCEEDED"}'
