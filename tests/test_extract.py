from hydraclaim.extract import _update_active, build_messages, parse_claims
from hydraclaim.extract import _reference_date

import pytest

SESSION = {
    "session_id": "s6",
    "started_at": "2026-05-18T09:00:00+00:00",
    "messages": [
        {
            "msg_id": "s6-m001",
            "ts": "2026-05-18T09:05:00+00:00",
            "author": "Meeting notes",
            "source_kind": "meeting",
            "channel": "fireflies",
            "text": "Final call in roadmap review: the launch deadline locks at October 17.",
        },
        {
            "msg_id": "s6-m002",
            "ts": "2026-05-18T09:07:00+00:00",
            "author": "Asha Rao",
            "source_kind": "slack",
            "channel": "#general",
            "text": "Standup notes: backend pair continuing on the search indexing bug.",
        },
    ],
}

ENTITIES = [
    {"name": "product launch", "type": "project", "aliases": ["the launch"]},
    {"name": "Mina Okafor", "type": "person", "aliases": ["Mina"]},
]

GOOD_RESPONSE = {
    "claims": [
        {
            "subject": "product launch",
            "predicate": "deadline",
            "value": "2026-10-17",
            "valid_from": "2026-05-18",
            "quote": "the launch deadline locks at October 17",
            "author": "Meeting notes",
            "source_kind": "meeting",
            "session_id": "s6",
            "msg_id": "s6-m001",
            "explicitness": 1.0,
            "confidence": 0.95,
            "supersedes": "deadline_drift:s3:x1",
        }
    ]
}


def test_build_messages_injects_context():
    active = [
        {
            "id": "deadline_drift:s3:x1",
            "subject": "product launch",
            "predicate": "deadline",
            "value": "2026-10-10",
            "valid_from": "2026-05-10",
            "source_kind": "slack",
            "author": "Mina Okafor",
        }
    ]
    messages = build_messages(SESSION, ENTITIES, active)
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    system, user = messages[0]["content"], messages[1]["content"]
    assert "deadline" in system and "owned_by" in system  # predicate vocab injected
    assert '"the launch"' in user  # aliases injected
    assert "deadline_drift:s3:x1" in user  # active claim id injected
    assert "s6-m001" in user  # message ids present


def test_reference_date_rejects_invalid_timestamp():
    with pytest.raises(ValueError, match="session timestamp"):
        _reference_date({"messages": [{"ts": "not-a-date"}]})


def test_parse_claims_rejects_unparseable_date_predicate_value():
    with pytest.raises(ValueError, match="deadline.*not-a-date"):
        parse_claims(_single({"value": "not-a-date"}), SESSION)


def test_build_messages_empty_active_claims():
    user = build_messages(SESSION, ENTITIES, [])[1]["content"]
    assert "(none)" in user


def test_parse_claims_roundtrip():
    drafts, warnings = parse_claims(GOOD_RESPONSE, SESSION, ACTIVE)
    assert warnings == []
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["predicate"] == "deadline"
    assert draft["value"] == "2026-10-17"
    assert draft["supersedes"] == "deadline_drift:s3:x1"
    assert draft["session_id"] == "s6"


ACTIVE = [
    {
        "id": "deadline_drift:s3:x1",
        "subject": "product launch",
        "predicate": "deadline",
        "value": "2026-10-10",
        "valid_from": "2026-05-10",
        "source_kind": "slack",
        "author": "Mina Okafor",
    }
]


def _single(overrides):
    claim = dict(GOOD_RESPONSE["claims"][0])
    claim.update(overrides)
    return {"claims": [claim]}


def test_drops_out_of_vocab_predicate():
    drafts, warnings = parse_claims(_single({"predicate": "celebrates"}), SESSION)
    assert drafts == [] and len(warnings) == 1


def test_drops_ungrounded_quote():
    drafts, warnings = parse_claims(_single({"quote": "not in the message"}), SESSION)
    assert drafts == [] and "quote" in warnings[0]


def test_drops_unknown_msg_id():
    drafts, warnings = parse_claims(_single({"msg_id": "s6-m999"}), SESSION)
    assert drafts == [] and "msg_id" in warnings[0]


def test_coerces_bad_scores_to_defaults():
    drafts, warnings = parse_claims(
        _single({"confidence": "high", "explicitness": None}), SESSION, ACTIVE
    )
    assert len(drafts) == 1
    assert drafts[0]["confidence"] == 0.5
    assert drafts[0]["explicitness"] == 1.0
    assert len(warnings) == 2


def test_empty_claims_list():
    drafts, warnings = parse_claims({"claims": []}, SESSION)
    assert drafts == [] and warnings == []


def test_update_active_drops_superseded():
    active = [
        {
            "id": "a:1",
            "subject": "product launch",
            "predicate": "deadline",
            "value": "2026-10-10",
            "valid_from": "2026-05-10",
            "source_kind": "slack",
            "author": "Mina Okafor",
        }
    ]
    drafts = [
        {
            "id": "a:2",
            "subject": "product launch",
            "predicate": "deadline",
            "value": "2026-10-17",
            "valid_from": "2026-05-18",
            "source_kind": "meeting",
            "author": "Meeting notes",
            "supersedes": "a:1",
        }
    ]
    updated = _update_active(active, drafts)
    assert [c["id"] for c in updated] == ["a:2"]
