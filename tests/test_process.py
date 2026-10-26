from __future__ import annotations

import pytest

from hydraclaim import process
from hydraclaim.extract import extract_session
from hydraclaim.source_events import _content_hash
from hydraclaim.subject_context import canonical_subject_context, subject_context_digest


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
        self, event_key, provider, model, prompt_version, *, reprocess, **context_meta
    ):
        action = ("start", event_key, provider, model, prompt_version)
        if context_meta:
            action += (context_meta,)
        self.actions.append(action)
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
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: _event())
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
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
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
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
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


def _trusted_context(*, actor_ref="whatsapp:owner", role="user"):
    messages = [
        {
            "msg_id": "q:42",
            "ts": "2026-08-20T10:30:00+00:00",
            "author": "Owner display",
            "text": "I prefer tea.",
            "source_actor_ref": "discord:42",
            "actor_ref": actor_ref,
            "role": role,
        }
    ]
    if role == "user":
        messages.append(
            {
                "msg_id": "r:9",
                "ts": "2026-08-20T10:30:02+00:00",
                "author": "Jarvis",
                "text": "Tea is noted.",
                "actor_ref": "jarvis:assistant",
                "role": "assistant",
            }
        )
    else:
        messages[0]["ts"] = "2026-08-20T10:30:02+00:00"
    return {
        "version": 1,
        "producer": "jarvis-recorder",
        "subjects": [
            {
                "actor_ref": "whatsapp:owner",
                "name": "owner",
                "aliases": ["owner"],
                "type": "unknown",
            }
        ],
        "messages": messages,
    }


def _context_event(context):
    event = _event()["event"]
    event["author"] = "Conversation"
    event["occurred_at"] = max(message["ts"] for message in context["messages"])
    event["content"] = "\n".join(
        f"{message['author']}: {message['text']}" for message in context["messages"]
    )
    event["subject_context"] = context
    event["subject_context_digest"] = subject_context_digest(context)
    event["content_hash"] = _content_hash(event)
    return {"event": event}


def _context_draft(msg_id="q:42", author="Owner display"):
    return {
        "id": "draft-1",
        "subject": "owner",
        "predicate": "likes",
        "value": "tea",
        "valid_from": "2026-08-20",
        "quote": "I prefer tea.",
        "author": author,
        "source_kind": "slack",
        "session_id": "event",
        "msg_id": msg_id,
        "explicitness": 1.0,
        "confidence": 1.0,
        "supersedes": None,
    }


def test_context_event_preserves_structured_messages_and_private_provenance(
    monkeypatch,
):
    event = _context_event(_trusted_context())
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
    seen = {}

    def extractor(session, roster, _active):
        seen["session"] = session
        seen["roster"] = roster
        return [_context_draft()], []

    def plan_writes(_drafts, _active, roster, *, id_prefix):
        seen["plan_roster"] = roster
        seen["id_prefix"] = id_prefix
        return {"create": [{"id": "draft-1"}]}

    monkeypatch.setattr(process, "plan_writes", plan_writes)

    process.process_event(
        object(),
        "event",
        extractor=extractor,
        provider="jarvis-codex",
        model="codex-test",
    )

    messages = seen["session"]["messages"]
    assert [(m["msg_id"], m["role"], m["eligible"]) for m in messages] == [
        ("q:42", "user", True),
        ("r:9", "assistant", False),
    ]
    assert messages[0]["speaker_name"] == "owner"
    assert messages[1]["speaker_name"] == "assistant"
    assert all("actor_ref" not in message for message in messages)
    assert seen["roster"] == [
        {"name": "owner", "type": "unknown", "aliases": ["owner"]}
    ]
    assert seen["plan_roster"] is seen["roster"]
    assert seen["id_prefix"] == "event"
    assert _Writer.calls[0][0][2] is seen["roster"]
    assert _Store.actions[0][-1] == {
        "subject_context_digest": subject_context_digest(_trusted_context()),
        "subject_context_provenance": "captured",
    }


@pytest.mark.parametrize("text", ["", " \t\n"])
def test_context_processing_keeps_empty_messages_beside_eligible_owner_evidence(
    monkeypatch, text
):
    context = _trusted_context()
    context["messages"].append(
        {
            **context["messages"][0],
            "msg_id": "q:43",
            "ts": "2026-08-20T10:30:03+00:00",
            "text": text,
        }
    )
    event = _context_event(context)
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
    seen = []

    def extractor(session, _entities, _active):
        seen.extend(session["messages"])
        return [_context_draft()], []

    result = process.process_event(
        object(),
        "event",
        extractor=extractor,
        provider="jarvis-codex",
        model="codex-test",
    )

    assert result["status"] == "SUCCEEDED"
    assert [(message["msg_id"], message["text"]) for message in seen] == [
        ("q:42", "I prefer tea."),
        ("r:9", "Tea is noted."),
        ("q:43", text),
    ]
    assert [(message["role"], message["eligible"]) for message in seen] == [
        ("user", True),
        ("assistant", False),
        ("user", True),
    ]


def test_context_roster_keeps_existing_graph_canonical_spelling():
    roster, names = process._subject_context_roster(
        _trusted_context(), [{"subject": "Owner"}]
    )

    assert roster == [{"name": "Owner", "type": "unknown", "aliases": ["owner"]}]
    assert names["whatsapp:owner"] == "Owner"


@pytest.mark.parametrize(
    "role,actor_ref", [("assistant", "jarvis:assistant"), ("user", "discord:42")]
)
def test_context_claim_from_assistant_or_unbound_user_fails_before_write(
    monkeypatch, role, actor_ref
):
    context = _trusted_context(actor_ref=actor_ref, role=role)
    event = _context_event(context)
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
    called = []

    expected_error = (
        "unbound user actor" if role == "user" else "eligible verified user message"
    )
    with pytest.raises(ValueError, match=expected_error):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: (
                called.append("extract") or [_context_draft()],
                [],
            ),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == ([] if role == "user" else ["extract"])
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")


def test_unbound_user_fails_before_extractor_even_when_model_would_return_no_claims(
    monkeypatch,
):
    event = _context_event(_trusted_context(actor_ref="discord:42"))
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
    called = []

    with pytest.raises(ValueError, match="unbound user actor"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extract") or ([], []),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")


def test_context_event_allows_assistant_only_no_claims(monkeypatch):
    context = _trusted_context(role="assistant")
    event = _context_event(context)
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)

    result = process.process_event(
        object(),
        "event",
        extractor=lambda *_args: ([], []),
        provider="jarvis-codex",
        model="codex-test",
    )

    assert result["status"] == "SUCCEEDED"
    assert _Writer.calls[0][0][0]["create"] == []


def test_unbound_user_failure_does_not_expose_private_actor_reference(monkeypatch):
    context = _trusted_context(actor_ref="discord:987654321")
    event = _context_event(context)
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)

    with pytest.raises(ValueError, match="unbound user actor") as failure:
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: pytest.fail("unbound source reaches model"),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert "discord:987654321" not in str(failure.value)
    assert "discord:987654321" not in str(_Store.actions[-1][-1])
    assert _Writer.calls == []


def test_explicit_old_event_context_records_operator_attestation(monkeypatch):
    context = _trusted_context()
    source = _context_event(context)["event"]
    source.pop("subject_context")
    source.pop("subject_context_digest")
    monkeypatch.setattr(
        process, "read_event", lambda _db, _key, **_kwargs: {"event": source}
    )

    result = process.process_event(
        object(),
        "event",
        extractor=lambda *_args: ([], []),
        provider="jarvis-codex",
        model="codex-test",
        subject_context=context,
    )

    assert result["status"] == "SUCCEEDED"
    assert _Store.actions[0][-1]["subject_context_provenance"] == "operator_attestation"
    assert _Store.actions[0][-1]["subject_context_digest"]
    assert _Store.actions[0][-1]["subject_context_json"] == canonical_subject_context(
        context
    )


def test_explicit_context_cannot_replace_captured_binding(monkeypatch):
    event = _context_event(_trusted_context())
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)

    with pytest.raises(ValueError, match="cannot replace captured"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: ([], []),
            provider="jarvis-codex",
            model="codex-test",
            subject_context=_trusted_context(actor_ref="whatsapp:other"),
        )

    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")
    assert _Writer.calls == []


def test_invalid_captured_context_fails_recorded_extract_before_model(monkeypatch):
    event = _context_event(_trusted_context())
    event["event"]["subject_context"]["messages"][0]["actor_ref"] = ""
    monkeypatch.setattr(process, "read_event", lambda _db, _key, **_kwargs: event)
    called = []

    with pytest.raises(ValueError, match="actor_ref"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extract") or ([], []),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")


@pytest.mark.parametrize("stored_context", [None, False, 0, ""])
def test_corrupt_present_context_cannot_select_legacy_extraction(
    monkeypatch, stored_context
):
    source = _event()["event"]
    source.update(
        subject_context=stored_context,
        subject_context_present=True,
        subject_context_digest="stored-digest",
    )
    monkeypatch.setattr(
        process, "read_event", lambda _db, _key, **_kwargs: {"event": source}
    )
    called = []

    with pytest.raises(ValueError, match="subject_context|captured"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extract") or ([], []),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Writer.calls == []
    assert _Store.actions[0][-1]["subject_context_digest"] == "stored-digest"
    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")


def test_context_digest_without_context_json_fails_after_recording_attempt(
    monkeypatch,
):
    source = _event()["event"]
    source.update(subject_context_present=True, subject_context_digest="stored-digest")
    monkeypatch.setattr(
        process, "read_event", lambda _db, _key, **_kwargs: {"event": source}
    )
    called = []

    with pytest.raises(ValueError, match="subject_context|captured"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extract") or ([], []),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Store.actions[0][-1]["subject_context_digest"] == "stored-digest"
    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")


@pytest.mark.parametrize("missing_field", ["subject_context_digest", "content_hash"])
def test_captured_context_requires_digest_and_source_hash(monkeypatch, missing_field):
    source = _context_event(_trusted_context())["event"]
    source.pop(missing_field)
    monkeypatch.setattr(
        process, "read_event", lambda _db, _key, **_kwargs: {"event": source}
    )
    called = []

    with pytest.raises(ValueError, match="integrity check"):
        process.process_event(
            object(),
            "event",
            extractor=lambda *_args: called.append("extract") or ([], []),
            provider="jarvis-codex",
            model="codex-test",
        )

    assert called == []
    assert _Writer.calls == []
    assert _Store.actions[-1][:3] == ("fail", "event:extraction:1", "EXTRACT")


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
