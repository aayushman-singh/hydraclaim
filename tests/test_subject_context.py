from __future__ import annotations

import pytest

from hydraclaim.subject_context import validate_subject_context


def context():
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
        "messages": [
            {
                "msg_id": "q:42",
                "ts": "2026-08-20T10:30:00+00:00",
                "author": "Owner display",
                "text": "I prefer tea.",
                "source_actor_ref": "discord:42",
                "actor_ref": "whatsapp:owner",
                "role": "user",
            },
            {
                "msg_id": "r:9",
                "ts": "2026-08-20T10:30:02+00:00",
                "author": "Jarvis",
                "text": "Understood.",
                "actor_ref": "jarvis:assistant",
                "role": "assistant",
            },
        ],
    }


def test_validate_subject_context_accepts_trusted_user_and_assistant_messages():
    checked = validate_subject_context(context())
    assert checked["subjects"][0]["actor_ref"] == "whatsapp:owner"
    assert checked["messages"][1]["role"] == "assistant"


def test_validate_subject_context_preserves_exact_source_author_and_text():
    value = context()
    value["messages"][0]["author"] = " Owner display "
    value["messages"][0]["text"] = " I prefer tea. "

    checked = validate_subject_context(value)

    assert checked["messages"][0]["author"] == " Owner display "
    assert checked["messages"][0]["text"] == " I prefer tea. "


def test_validate_subject_context_rejects_ambiguous_subject_names_and_aliases():
    value = context()
    value["subjects"].append(
        {"actor_ref": "other", "name": "Owner", "aliases": [], "type": "unknown"}
    )
    with pytest.raises(ValueError, match="ambiguous"):
        validate_subject_context(value)


def test_validate_subject_context_rejects_alias_that_matches_another_subject():
    value = context()
    value["subjects"].append(
        {
            "actor_ref": "other",
            "name": "teammate",
            "aliases": ["Owner"],
            "type": "unknown",
        }
    )
    with pytest.raises(ValueError, match="ambiguous"):
        validate_subject_context(value)


def test_validate_subject_context_rejects_duplicate_message_ids():
    value = context()
    value["messages"].append(dict(value["messages"][0]))
    with pytest.raises(ValueError, match="unique"):
        validate_subject_context(value)


def test_validate_subject_context_keeps_unbound_user_actor_ref():
    value = context()
    value["messages"][0]["actor_ref"] = "discord:42"
    assert validate_subject_context(value)["messages"][0]["actor_ref"] == "discord:42"
