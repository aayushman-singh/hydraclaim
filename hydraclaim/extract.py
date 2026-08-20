"""LLM claim extraction: session JSON -> structured claim drafts.

The LLM's only jobs are extraction and overwrite-linking. Contradiction
detection is deliberately NOT here — reconcile.py does it deterministically.

ClaimDraft contract (shared with reconcile.py):
    subject, predicate, value, valid_from (YYYY-MM-DD), quote, author,
    source_kind (slack|linear|meeting), session_id, msg_id,
    explicitness (0..1), confidence (0..1), supersedes (graph id or null)

CLI: python -m hydraclaim.extract SCENARIO_JSON [--emit drafts.json]
Threads state across sessions: drafts from earlier sessions become the
active-claims context for later ones (synthetic ids `{scenario}:x{N}`),
so the LLM can link overwrites to claims it created itself.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from hydraclaim.claims import PREDICATES, SOURCE_KINDS
from hydraclaim.errors import ValidationError


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_DATE_RE = re.compile(
    r"\b(?P<month>[a-z]{3,9})\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b|\b(?P<day2>\d{1,2})\s+(?P<month2>[a-z]{3,9})\b",
    re.IGNORECASE,
)

_DATE_PREDICATES = frozenset({"deadline"})


def _parse_timestamp(raw: object, *, label: str = "session timestamp") -> datetime:
    """Parse one package-accepted ISO timestamp or fail loudly."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"invalid {label}: {raw!r}")
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {raw!r}") from exc


def _reference_date(session: dict) -> date:
    """Return the session date from its timestamp, or raise on bad input."""
    raw = session.get("started_at")
    if raw is None and "messages" in session:
        messages = session.get("messages")
        if isinstance(messages, list) and messages:
            first_message = messages[0]
            raw = first_message.get("ts") if isinstance(first_message, dict) else None
    if raw is None:
        raw = session.get("ts")
    return _parse_timestamp(raw).date()


def _normalize_value(value: str, msg: dict, predicate: str = "deadline") -> str:
    """Convert natural-language dates in a value to YYYY-MM-DD.

    If the value is already ISO-like, leave it. Otherwise look for month/day
    patterns and resolve them using the message year.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid {predicate} value: {value!r}")
    value = value.strip()
    if predicate not in _DATE_PREDICATES:
        return value
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"invalid {predicate} value: {value!r}") from exc
    match = _DATE_RE.search(value)
    if not match:
        raise ValueError(f"invalid {predicate} value: {value!r}")
    ref = _reference_date({"messages": [msg]})
    month = match.group("month") or match.group("month2")
    day = match.group("day") or match.group("day2")
    try:
        month_num = _MONTHS[month.lower()]
        day_num = int(day)
        return date(ref.year, month_num, day_num).isoformat()
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid {predicate} value: {value!r}") from exc


def _validate_supersedes(raw: object, active_ids: set[str]) -> str | None:
    """Validate an explicit supersession target."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"supersedes must be a claim id string, got {type(raw).__name__}"
        )
    if raw not in active_ids:
        raise ValueError(f"supersedes target {raw!r} is not an active claim")
    return raw


def build_messages(
    session: dict, entities: list[dict], active_claims: list[dict]
) -> list[dict]:
    roster = [
        {"name": e["name"], "aliases": e.get("aliases", []), "type": e.get("type", "")}
        for e in entities
    ]
    active_lines = (
        "\n".join(
            f"  {c['id']} | {c['subject']} | {c['predicate']} | {c['value']} | {c['valid_from']}"
            for c in active_claims
        )
        or "  (none)"
    )
    message_lines = "\n".join(
        f"[{m['msg_id']}] {m['ts']} {m['author']} ({m['source_kind']}, {m['channel']}): {m['text']}"
        for m in session["messages"]
    )

    system = f"""You extract structured memory claims from chat logs for an agent memory system.

Rules:
- Only extract claims whose predicate is one of: {", ".join(sorted(PREDICATES))}.
- subject: use exactly one of the KNOWN ENTITIES names. Do not invent phrases
  like "product launch deadline"; use "product launch".
- value: the object of the claim, short and factual. Dates MUST be YYYY-MM-DD:
  convert natural-language dates using the message timestamp (a message dated
  2026-05-18 saying "October 17" becomes 2026-10-17). Never copy a natural
  date like "October 17" into value verbatim.
- quote: an exact substring copied verbatim from ONE message. msg_id: that message's id.
- author: the author of that message. source_kind: one of {", ".join(sorted(SOURCE_KINDS))} (copy from the message).
- session_id: copy the session id shown below.
- valid_from: the date (YYYY-MM-DD) the claim became true — usually the message date.
- explicitness 0..1 (1 = stated directly, lower = hedged or implied). confidence 0..1.
- supersedes: if a claim explicitly corrects or replaces one of the ACTIVE CLAIMS
  (signals like "correction", "actually", "moving", "taking over", "instead"),
  set it to that claim's id; otherwise null.
- Extract status claims when a message says a project/person is
  "on track", "at risk", "blocked", "complete", "delayed", "green", or "red".
- Never invent information. If nothing qualifies, return {{"claims": []}}.

Respond with strict JSON only, shape:
{{"claims": [{{"subject", "predicate", "value", "valid_from", "quote", "author",
"source_kind", "session_id", "msg_id", "explicitness", "confidence", "supersedes"}}]}}"""

    user = f"""KNOWN ENTITIES:
{json.dumps(roster, indent=2)}

ACTIVE CLAIMS (id | subject | predicate | value | valid_from):
{active_lines}

SESSION {session["session_id"]} MESSAGES:
{message_lines}"""

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _score(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {field}: {value!r}")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"invalid {field}: {value!r}; expected a number from 0 to 1")
    return score


def parse_claims(
    response_json: object, session: dict, active_claims: list[dict] | None = None
) -> tuple[list[dict], list[str]]:
    """Validate raw LLM output into drafts and record non-temporal warnings.

    Invalid temporal input raises so extraction cannot continue with an
    incorrect date.
    """
    if not isinstance(response_json, dict) or set(response_json) != {"claims"}:
        raise ValueError("malformed extraction root: expected object with claims")
    raw_claims = response_json["claims"]
    if not isinstance(raw_claims, list):
        raise ValueError("malformed extraction root: 'claims' must be a list")

    by_msg_id = {m["msg_id"]: m for m in session["messages"]}
    active_ids = {c["id"] for c in (active_claims or [])}
    drafts, warnings = [], []
    required_fields = {
        "subject",
        "predicate",
        "value",
        "valid_from",
        "quote",
        "author",
        "source_kind",
        "session_id",
        "msg_id",
        "explicitness",
        "confidence",
        "supersedes",
    }
    for i, raw in enumerate(raw_claims):
        where = f"claim[{i}] ({raw.get('predicate', '?') if isinstance(raw, dict) else '?'})"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: claim must be an object")
        unknown = sorted(set(raw) - required_fields)
        if unknown:
            raise ValueError(f"{where}: unsupported fields: {unknown}")
        missing = sorted(required_fields - set(raw))
        if missing:
            raise ValueError(f"{where}: missing fields: {missing}")
        if raw.get("predicate") not in PREDICATES:
            raise ValueError(f"{where}: unknown predicate {raw.get('predicate')!r}")
        if raw.get("source_kind") not in SOURCE_KINDS:
            raise ValueError(f"{where}: unknown source_kind {raw.get('source_kind')!r}")
        msg = by_msg_id.get(raw.get("msg_id", ""))
        if msg is None:
            raise ValueError(f"{where}: msg_id {raw.get('msg_id')!r} not in session")
        if raw["session_id"] != session["session_id"]:
            raise ValueError(f"{where}: session_id does not match session")
        if raw["source_kind"] != msg["source_kind"]:
            raise ValueError(f"{where}: source_kind does not match message")
        if not isinstance(raw["author"], str) or not raw["author"].strip():
            raise ValueError(f"{where}: author must be a non-empty string")
        if raw["author"] != msg["author"]:
            raise ValueError(f"{where}: author does not match message")
        quote = raw.get("quote")
        if not isinstance(quote, str) or not quote or quote not in msg["text"]:
            raise ValueError(f"{where}: quote not found verbatim in {msg['msg_id']}")
        raw_valid_from = raw.get("valid_from")
        try:
            if not isinstance(raw_valid_from, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", raw_valid_from
            ):
                raise ValueError
            valid_from = date.fromisoformat(raw_valid_from).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid valid_from date: {raw_valid_from!r}") from exc
        if not isinstance(raw["subject"], str) or not raw["subject"].strip():
            raise ValueError(f"{where}: subject must be a non-empty string")

        try:
            explicitness = _score(raw.get("explicitness"), "explicitness")
            confidence = _score(raw.get("confidence"), "confidence")
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from exc

        try:
            value = _normalize_value(raw["value"], msg, raw["predicate"])
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from exc
        supersedes = _validate_supersedes(raw["supersedes"], active_ids)

        drafts.append(
            {
                "subject": raw["subject"].strip(),
                "predicate": raw["predicate"],
                "value": value,
                "valid_from": valid_from,
                "quote": quote,
                "author": raw["author"],
                "source_kind": raw["source_kind"],
                "session_id": session["session_id"],
                "msg_id": msg["msg_id"],
                "explicitness": explicitness,
                "confidence": confidence,
                "supersedes": supersedes,
            }
        )
    return drafts, warnings


def extract_session(
    session: dict, entities: list[dict], active_claims: list[dict]
) -> tuple[list[dict], list[str]]:
    from hydraclaim.llm import chat_json  # lazy: no network at import time

    response = chat_json(build_messages(session, entities, active_claims))
    return parse_claims(response, session, active_claims)


def _update_active(active: list[dict], drafts: list[dict]) -> list[dict]:
    """Thread extraction state into the next session's prompt context:
    append new drafts, drop claims they explicitly superseded."""
    superseded = {d["supersedes"] for d in drafts if d.get("supersedes")}
    kept = [c for c in active if c["id"] not in superseded]
    kept.extend(
        {
            "id": d["id"],
            "subject": d["subject"],
            "predicate": d["predicate"],
            "value": d["value"],
            "valid_from": d["valid_from"],
            "source_kind": d["source_kind"],
            "author": d["author"],
        }
        for d in drafts
    )
    return kept


def _required_string(value: object, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")


def validate_extraction_document(document: object) -> dict:
    """Validate the session document before extraction accesses its fields."""
    if not isinstance(document, dict):
        raise ValidationError("invalid extraction document: root must be an object")

    errors: list[str] = []
    _required_string(document.get("scenario_id"), "scenario_id", errors)

    entities = document.get("entities")
    if not isinstance(entities, list):
        errors.append("entities must be a list")
    else:
        for index, entity in enumerate(entities):
            path = f"entities[{index}]"
            if not isinstance(entity, dict):
                errors.append(f"{path} must be an object")
                continue
            _required_string(entity.get("name"), f"{path}.name", errors)
            if "type" in entity and not isinstance(entity["type"], str):
                errors.append(f"{path}.type must be a string")
            aliases = entity.get("aliases", [])
            if not isinstance(aliases, list) or any(
                not isinstance(alias, str) for alias in aliases
            ):
                errors.append(f"{path}.aliases must be a list of strings")

    sessions = document.get("sessions")
    if not isinstance(sessions, list):
        errors.append("sessions must be a list")
    else:
        message_fields = (
            "msg_id",
            "ts",
            "author",
            "source_kind",
            "channel",
            "text",
        )
        for session_index, session in enumerate(sessions):
            session_path = f"sessions[{session_index}]"
            if not isinstance(session, dict):
                errors.append(f"{session_path} must be an object")
                continue
            _required_string(
                session.get("session_id"), f"{session_path}.session_id", errors
            )
            if "started_at" in session:
                _required_string(
                    session.get("started_at"), f"{session_path}.started_at", errors
                )
            messages = session.get("messages")
            if not isinstance(messages, list):
                errors.append(f"{session_path}.messages must be a list")
                continue
            for message_index, message in enumerate(messages):
                message_path = f"{session_path}.messages[{message_index}]"
                if not isinstance(message, dict):
                    errors.append(f"{message_path} must be an object")
                    continue
                for field in message_fields:
                    _required_string(
                        message.get(field), f"{message_path}.{field}", errors
                    )
                if message.get("source_kind") not in SOURCE_KINDS:
                    errors.append(
                        f"{message_path}.source_kind is not supported: "
                        f"{message.get('source_kind')!r}"
                    )

    if errors:
        raise ValidationError("invalid extraction document: " + "; ".join(errors))
    return document


def main(argv: Sequence[str] | None = None) -> int | None:
    from hydraclaim.config import command_epilog

    parser = argparse.ArgumentParser(
        prog="hydraclaim extract",
        epilog=command_epilog(llm="required"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("scenario", help="scenario JSON file (sessions + entities)")
    parser.add_argument("--emit", metavar="OUT_JSON", help="write drafts to this file")
    args = parser.parse_args(argv)

    from hydraclaim import config

    config.require_settings(llm=True)

    doc = validate_extraction_document(
        json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    )
    scen = doc["scenario_id"]
    active: list[dict] = []
    all_drafts: list[dict] = []

    for session in doc["sessions"]:
        drafts, warnings = extract_session(session, doc["entities"], active)
        for warning in warnings:
            print(f"warn [{session['session_id']}]: {warning}", file=sys.stderr)
        for draft in drafts:
            draft["id"] = f"{scen}:x{len(all_drafts) + 1}"
            all_drafts.append(draft)
        active = _update_active(active, drafts)
        print(
            f"{session['session_id']}: {len(drafts)} claim(s), {len(warnings)} warning(s)"
        )

    if args.emit:
        out = {"scenario_id": scen, "drafts": all_drafts}
        Path(args.emit).write_text(
            json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {len(all_drafts)} draft(s) to {args.emit}")


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("extract", main))
