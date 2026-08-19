"""Offline tests for hydraclaim.slack_import."""

from __future__ import annotations

from hydraclaim.slack_import import _strip_slack_formatting, parse_slack_export


def test_strip_user_mention():
    assert _strip_slack_formatting("Hey <@U1234|alice>, check this") == "Hey alice, check this"
    assert _strip_slack_formatting("Hey <@U1234>, check this") == "Hey someone, check this"


def test_strip_url_with_label():
    assert _strip_slack_formatting("See <https://example.com|the docs>") == "See the docs"


def test_strip_bare_url():
    assert _strip_slack_formatting("Link: <https://example.com>") == "Link: https://example.com"


def test_strip_channel_ref():
    assert _strip_slack_formatting("Check <#C123|general>") == "Check general"


def test_strip_html_entities():
    assert _strip_slack_formatting("A &amp; B &lt; C &gt; D") == "A & B < C > D"


def test_parse_empty():
    assert parse_slack_export([]) == []


def test_parse_skips_system_messages():
    msgs = [
        {"ts": "1716000000.000", "text": "joined", "subtype": "channel_join"},
        {"ts": "1716000001.000", "text": "hello world", "user": "alice"},
    ]
    sessions = parse_slack_export(msgs, "general")
    assert len(sessions) == 1
    assert len(sessions[0]["messages"]) == 1
    assert sessions[0]["messages"][0]["text"] == "hello world"


def test_parse_groups_by_day():
    msgs = [
        {"ts": "1716000000.000", "text": "morning msg", "user": "alice"},
        {"ts": "1716086400.000", "text": "next day msg", "user": "bob"},
    ]
    sessions = parse_slack_export(msgs, "dev")
    assert len(sessions) == 2
    assert all(s["session_id"].startswith("slack-dev-") for s in sessions)


def test_parse_message_fields():
    msgs = [{"ts": "1716000000.000", "text": "the deadline is Oct 3",
             "user": "alice", "user_profile": {"real_name": "Alice Smith"}}]
    sessions = parse_slack_export(msgs, "proj")
    m = sessions[0]["messages"][0]
    assert m["author"] == "Alice Smith"
    assert m["source_kind"] == "slack"
    assert m["channel"] == "proj"
    assert m["text"] == "the deadline is Oct 3"


def test_parse_skips_empty_text():
    msgs = [
        {"ts": "1716000000.000", "text": "", "user": "alice"},
        {"ts": "1716000001.000", "text": "  ", "user": "bob"},
        {"ts": "1716000002.000", "text": "real content", "user": "carol"},
    ]
    sessions = parse_slack_export(msgs)
    total_msgs = sum(len(s["messages"]) for s in sessions)
    assert total_msgs == 1
