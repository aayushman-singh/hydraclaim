"""Expand scenario specs into session JSON + ground truth, deterministically.

Output document (one file per scenario):

    scenario_id, description, seed
    entities     [{name, type, aliases}]
    sessions     [{session_id, started_at, messages: [{msg_id, ts, author,
                   source_kind, channel, text}]}]
    ground_truth.claims  bitemporal claims; superseded claims carry
                         valid_to = valid_from of the superseding claim
    ground_truth.qa      [{question, answer, qtype, gold_claim_keys}]

Filler messages carry no claims — they are noise for the future LLM
extractor. Scale their count up when we need LongMemEval-sized histories.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from hydraclaim.claims import validate_scenario
from hydraclaim.generate import scenarios

BASE_DATE = date(2026, 5, 4)

FILLER_TEXTS = [
    "Standup notes: backend pair continuing on the search indexing bug.",
    "Reminder that expense reports close on Friday.",
    "Design crit moved to 3pm, same room.",
    "Anyone else seeing flaky CI on the web repo this morning?",
    "New hire orientation starts Monday — welcome Ana to the data team.",
    "Postmortem doc for last week's cache incident is published.",
    "Please update your on-call preferences in the roster sheet.",
    "Marketing sync notes are in the shared drive.",
    "The office wifi upgrade happens Saturday morning.",
    "Quarterly planning survey closes tonight, two minutes to fill in.",
    "Frontend guild: migration guide for the new table component is out.",
    "Book club moved to the last Thursday of the month.",
]

FILLER_AUTHORS = ["Asha Rao", "Lee Park", "Ravi Nair", "Noor Haddad"]


def _iso(d: date) -> str:
    return d.isoformat()


def _ts(started: datetime, minute: int) -> str:
    return (started + timedelta(minutes=minute)).isoformat()


def expand_scenario(spec: dict, seed: int) -> dict:
    errors = validate_scenario(spec)
    if errors:
        raise ValueError(f"invalid scenario {spec.get('scenario_id')!r}: {errors}")

    rng = random.Random(f"{seed}:{spec['scenario_id']}")

    # Materialize claims with absolute dates.
    claims: dict[str, dict] = {}
    claim_session: dict[str, str] = {}
    for event in spec["events"]:
        for claim in event["claims"]:
            c = dict(claim)
            c["valid_from"] = _iso(BASE_DATE + timedelta(days=c.pop("day")))
            c["valid_to"] = None
            c["status"] = "active"
            claims[c["key"]] = c
            claim_session[c["key"]] = event["session"]

    # Supersession closes the validity window of the overwritten claim.
    for c in claims.values():
        if c["supersedes"]:
            target = claims[c["supersedes"]]
            target["status"] = "superseded"
            target["valid_to"] = c["valid_from"]

    # Group events into sessions and interleave claim-free filler messages.
    sessions_by_id: dict[str, list[dict]] = {}
    days_by_id: dict[str, int] = {}
    for event in spec["events"]:
        sessions_by_id.setdefault(event["session"], []).append(event)
        days_by_id[event["session"]] = event["day"]

    sessions = []
    for sid in sorted(sessions_by_id):
        started = datetime.combine(
            BASE_DATE + timedelta(days=days_by_id[sid]),
            time(9, 0),
            tzinfo=timezone.utc,
        )
        messages = []
        minute = 0
        for event in sessions_by_id[sid]:
            minute += rng.randint(2, 7)
            messages.append(
                {
                    "msg_id": "",
                    "ts": _ts(started, minute),
                    "author": event["author"],
                    "source_kind": event["source_kind"],
                    "channel": event["channel"],
                    "text": event["text"],
                }
            )
            for filler in rng.sample(FILLER_TEXTS, k=rng.randint(1, 3)):
                minute += rng.randint(1, 5)
                messages.append(
                    {
                        "msg_id": "",
                        "ts": _ts(started, minute),
                        "author": rng.choice(FILLER_AUTHORS),
                        "source_kind": "slack",
                        "channel": "#general",
                        "text": filler,
                    }
                )
        messages.sort(key=lambda m: m["ts"])
        for i, msg in enumerate(messages, start=1):
            msg["msg_id"] = f"{sid}-m{i:03d}"
        sessions.append(
            {"session_id": sid, "started_at": started.isoformat(), "messages": messages}
        )

    # Link each claim to the message its quote came from.
    all_messages = [m for s in sessions for m in s["messages"]]
    for c in claims.values():
        c["session_id"] = claim_session[c["key"]]
        c["msg_id"] = next(
            (m["msg_id"] for m in all_messages if c["quote"] in m["text"]),
            "",
        )

    return {
        "scenario_id": spec["scenario_id"],
        "description": spec["description"],
        "seed": seed,
        "entities": spec["entities"],
        "sessions": sessions,
        "ground_truth": {
            "claims": [claims[k] for k in sorted(claims)],
            "qa": spec["qa"],
        },
    }


def write_dataset(out_dir: str | Path, seed: int = 42) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for builder in scenarios.SCENARIOS:
        doc = expand_scenario(builder(), seed)
        path = out / f"{doc['scenario_id']}.json"
        path.write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written
