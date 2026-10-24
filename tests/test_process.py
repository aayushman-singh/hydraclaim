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
    calls = []

    def __init__(self, _db):
        pass

    def apply_plan(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture(autouse=True)
def _patch_process_dependencies(monkeypatch):
    _Store.actions = []
    _Writer.calls = []
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
    assert seen[1:] == [
        [{"name": "Asha Rao", "type": "unknown", "aliases": []}],
        [],
    ]
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


def test_process_event_passes_one_live_event_roster_through_all_stages(monkeypatch):
    active = [
        {"subject": "Asha Rao"},
        {"subject": "Project Atlas"},
    ]
    event = _event()
    event["event"]["author"] = "asha rao"
    monkeypatch.setattr(process, "read_event", lambda _db, _key: event)
    monkeypatch.setattr(process, "fetch_active_claims", lambda _db: active)
    seen = {}

    def extractor(_session, entities, _active):
        seen["extractor_roster"] = entities
        return [{"id": "draft-1", "subject": "Asha Rao"}], []

    def plan_writes(drafts, active_claims, roster, *, id_prefix):
        seen["plan_roster"] = roster
        seen["drafts"] = drafts
        seen["active"] = active_claims
        seen["id_prefix"] = id_prefix
        return {
            "create": [{"id": "draft-1", "subject": "Asha Rao"}],
            "supersede": [],
            "contradict": [],
            "duplicates": 0,
            "warnings": [],
        }

    monkeypatch.setattr(process, "plan_writes", plan_writes)

    result = process.process_event(
        object(),
        "event",
        extractor=extractor,
        provider="jarvis-codex",
        model="codex-test",
    )

    roster = seen["extractor_roster"]
    assert roster == [
        {"name": "Asha Rao", "type": "unknown", "aliases": []},
        {"name": "Project Atlas", "type": "unknown", "aliases": []},
    ]
    assert seen["plan_roster"] is roster
    writer_args, writer_kwargs = _Writer.calls[0]
    assert writer_args[0]["create"] == [{"id": "draft-1", "subject": "Asha Rao"}]
    assert writer_args[2] is roster
    assert seen["active"] is active
    assert seen["id_prefix"] == "event"
    assert writer_args[1] == "event"
    assert writer_kwargs == {
        "extraction_key": "event:extraction:1",
        "source_event_keys": {"event": "event"},
    }
    assert result["status"] == "SUCCEEDED"
    assert _Store.actions[-1] == (
        "complete",
        "event:extraction:1",
        ["draft-1"],
    )


def test_live_event_roster_adds_unknown_author_after_active_subjects():
    roster = process._live_event_roster("New Author", [{"subject": "Existing Project"}])

    assert [entity["name"] for entity in roster] == [
        "Existing Project",
        "New Author",
    ]


def test_live_event_roster_uses_lower_identity_without_casefold():
    roster = process._live_event_roster(
        "Owner", [{"subject": "Straße"}, {"subject": "STRASSE"}]
    )

    assert [entity["name"] for entity in roster] == [
        "Straße",
        "STRASSE",
        "Owner",
    ]


def test_process_event_rejects_ambiguous_active_subject_spelling(monkeypatch):
    monkeypatch.setattr(
        process,
        "fetch_active_claims",
        lambda _db: [{"subject": "Asha Rao"}, {"subject": "asha rao"}],
    )
    called = []

    with pytest.raises(ValueError, match="ambiguous canonical spelling"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extractor"),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == (
        "fail",
        "event:extraction:1",
        "EXTRACT",
    )


def test_process_event_rejects_unknown_subject_before_reconcile_or_write(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        process,
        "plan_writes",
        lambda *_args, **_kwargs: calls.append("plan"),
    )

    with pytest.raises(ValueError, match="subject.*roster"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: ([{"subject": "asha rao"}], []),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert calls == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == (
        "fail",
        "event:extraction:1",
        "EXTRACT",
    )


def test_process_event_rejects_extractor_warnings_before_plan_or_write(monkeypatch):
    calls = []
    monkeypatch.setattr(
        process,
        "plan_writes",
        lambda *_args, **_kwargs: calls.append("plan"),
    )

    with pytest.raises(RuntimeError, match="extractor warnings"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: ([], ["invalid date"]),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert calls == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == (
        "fail",
        "event:extraction:1",
        "EXTRACT",
    )


def test_process_event_records_active_context_failure_as_extraction_failure(
    monkeypatch,
):
    failure = RuntimeError("active claims unavailable")
    called = []
    monkeypatch.setattr(
        process, "fetch_active_claims", lambda _db: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(RuntimeError, match="active claims unavailable"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extractor"),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == (
        "fail",
        "event:extraction:1",
        "EXTRACT",
    )
    assert _Store.actions[-1][3] is failure


@pytest.mark.parametrize(
    ("author", "active", "message"),
    [
        ("  ", [], "source author must be a non-empty string"),
        ("Asha Rao", [{"subject": "  "}], "entity names must be non-empty strings"),
    ],
)
def test_process_event_records_invalid_roster_as_extraction_failure(
    monkeypatch, author, active, message
):
    event = _event()
    event["event"]["author"] = author
    monkeypatch.setattr(process, "read_event", lambda _db, _key: event)
    monkeypatch.setattr(process, "fetch_active_claims", lambda _db: active)
    called = []

    with pytest.raises(ValueError, match=message):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extractor"),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Store.actions[-1][:3] == (
        "fail",
        "event:extraction:1",
        "EXTRACT",
    )


def test_process_event_accepts_no_claims_when_roster_is_valid():
    result = process.process_event(
        object(),
        "event",
        extractor=lambda _session, entities, _active: (
            [] if entities else pytest.fail("expected a non-empty roster"),
            [],
        ),
        provider="jarvis-codex",
        model="codex-test",
    )

    assert result["status"] == "SUCCEEDED"
    assert _Store.actions[-1] == ("complete", "event:extraction:1", [])


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
