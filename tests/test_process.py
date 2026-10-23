from __future__ import annotations

import pytest

from hydraclaim import process


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

    def start_extraction(self, event_key, provider, model, prompt_version, *, reprocess):
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
    monkeypatch.setattr(process, "plan_writes", lambda *_args, **_kwargs: {"create": []})
    monkeypatch.setattr(process, "GraphWriter", _Writer)


def test_process_event_uses_required_extractor_and_records_explicit_metadata():
    seen = []

    def extractor(messages):
        seen.extend(messages)
        return {"claims": []}

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
    assert seen[0]["role"] == "system"
    assert "Launch moves to Monday." in seen[1]["content"]
    assert _Store.actions == [
        ("start", "event", "jarvis-codex", "codex-test", "extract-v1"),
        ("complete", "event:extraction:1", []),
    ]


def test_process_event_records_extractor_failure_and_stops():
    failure = RuntimeError("extractor unavailable")

    def extractor(_messages):
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
