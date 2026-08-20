"""Slack export JSON to HydraClaim session format converter.

Accepts a Slack channel export (array of messages) and converts to
session documents grouped by day. Strips Slack-specific formatting.

CLI: python -m hydraclaim.slack_import INPUT.json --channel general --out sessions/
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone


def _strip_slack_formatting(text: str) -> str:
    """Remove Slack-specific markup: user mentions, URL labels, channel refs."""
    text = re.sub(
        r"<@[A-Z0-9]+(?:\|([^>]+))?>", lambda m: m.group(1) or "someone", text
    )
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(
        r"<#[A-Z0-9]+(?:\|([^>]+))?>", lambda m: m.group(1) or "#channel", text
    )
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return text.strip()


def _author_name(msg: dict) -> str:
    """Best-effort author name from a Slack message."""
    profile = msg.get("user_profile", {})
    if isinstance(profile, dict):
        name = profile.get("real_name") or profile.get("display_name")
        if name:
            return name
    return msg.get("user", "unknown")


def _msg_timestamp(msg: dict) -> str:
    """Parse Slack's ``ts`` field and return an ISO-8601 timestamp."""
    raw = msg.get("ts")
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError) as exc:
        raise ValueError(f"invalid Slack timestamp: {raw!r}") from exc


def parse_slack_export(
    messages: list[dict],
    channel: str = "general",
) -> list[dict]:
    """Convert Slack messages to HydraClaim session docs, grouped by day.

    Returns a list of session dicts sorted by date, each containing:
        session_id, messages: [{msg_id, ts, author, source_kind, channel, text}]
    """
    by_day: dict[str, list[dict]] = defaultdict(list)

    for msg in messages:
        if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
            continue
        text = msg.get("text", "")
        if not text or not text.strip():
            continue

        timestamp = _msg_timestamp(msg)
        dt = datetime.fromisoformat(timestamp)
        day = dt.date().isoformat()
        author = _author_name(msg)
        clean_text = _strip_slack_formatting(text)

        by_day[day].append(
            {
                "msg_id": f"slack-{channel}-{msg.get('ts', '0').replace('.', '-')}",
                "ts": dt.isoformat(),
                "author": author,
                "source_kind": "slack",
                "channel": channel,
                "text": clean_text,
            }
        )

    sessions = []
    for day in sorted(by_day.keys()):
        msgs = sorted(by_day[day], key=lambda m: m["ts"])
        sessions.append(
            {
                "session_id": f"slack-{channel}-{day}",
                "messages": msgs,
            }
        )

    return sessions
