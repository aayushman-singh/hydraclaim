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
import re
import sys
from datetime import date, datetime
from pathlib import Path

from hydraclaim.claims import PREDICATES, SOURCE_KINDS


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_RE = re.compile(
    r"\b(?P<month>[a-z]{3,9})\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b|\b(?P<day2>\d{1,2})\s+(?P<month2>[a-z]{3,9})\b",
    re.IGNORECASE,
)


def _reference_date(msg: dict) -> date:
    """Best-effort reference date from a message timestamp."""
    ts = msg.get("ts", "")
    try:
        return date.fromisoformat(ts[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return date.today()


def _normalize_value(value: str, msg: dict) -> str:
    """Convert natural-language dates in a value to YYYY-MM-DD.

    If the value is already ISO-like, leave it. Otherwise look for month/day
    patterns and resolve them using the message year.
    """
    value = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    match = _DATE_RE.search(value)
    if not match:
        return value
    ref = _reference_date(msg)
    month = match.group("month") or match.group("month2")
    day = match.group("day") or match.group("day2")
    try:
        month_num = _MONTHS[month.lower()]
        day_num = int(day)
        normalized = date(ref.year, month_num, day_num).isoformat()
        return normalized
    except (KeyError, ValueError):
        return value


def _validate_supersedes(raw: object, active_ids: set[str]) -> tuple[str | None, str | None]:
    """Return (target_id, warning) tuple."""
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, str):
        return None, f"supersedes must be a claim id string, got {type(raw).__name__}"
    if raw not in active_ids:
        return None, f"supersedes target {raw!r} is not an active claim, ignored"
    return raw, None


def build_messages(
    session: dict, entities: list[dict], active_claims: list[dict]
) -> list[dict]:
    roster = [
        {"name": e["name"], "aliases": e.get("aliases", []), "type": e.get("type", "")}
        for e in entities
    ]
    active_lines = "\n".join(
        f"  {c['id']} | {c['subject']} | {c['predicate']} | {c['value']} | {c['valid_from']}"
        for c in active_claims
    ) or "  (none)"
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

SESSION {session['session_id']} MESSAGES:
{message_lines}"""

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _score(value: object, default: float) -> tuple[float, bool]:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default, False
    return min(max(f, 0.0), 1.0), True


def parse_claims(response_json: object, session: dict,
                 active_claims: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """Validate raw LLM output into drafts. Never raises on bad LLM output —
    drops the offending claim and records a human-readable warning."""
    if isinstance(response_json, dict):
        raw_claims = response_json.get("claims", [])
    elif isinstance(response_json, list):
        raw_claims = response_json
    else:
        return [], [f"unexpected LLM response type: {type(response_json).__name__}"]
    if not isinstance(raw_claims, list):
        return [], ["'claims' is not a list"]

    by_msg_id = {m["msg_id"]: m for m in session["messages"]}
    active_ids = {c["id"] for c in (active_claims or [])}
    drafts, warnings = [], []
    for i, raw in enumerate(raw_claims):
        where = f"claim[{i}] ({raw.get('predicate', '?') if isinstance(raw, dict) else '?'})"
        if not isinstance(raw, dict):
            warnings.append(f"{where}: not an object, dropped")
            continue
        if raw.get("predicate") not in PREDICATES:
            warnings.append(f"{where}: unknown predicate {raw.get('predicate')!r}, dropped")
            continue
        if raw.get("source_kind") not in SOURCE_KINDS:
            warnings.append(f"{where}: unknown source_kind {raw.get('source_kind')!r}, dropped")
            continue
        msg = by_msg_id.get(raw.get("msg_id", ""))
        if msg is None:
            warnings.append(f"{where}: msg_id {raw.get('msg_id')!r} not in session, dropped")
            continue
        quote = raw.get("quote")
        if not isinstance(quote, str) or not quote or quote not in msg["text"]:
            warnings.append(f"{where}: quote not found verbatim in {msg['msg_id']}, dropped")
            continue
        try:
            valid_from = date.fromisoformat(str(raw.get("valid_from", ""))[:10]).isoformat()
        except ValueError:
            warnings.append(f"{where}: bad valid_from {raw.get('valid_from')!r}, dropped")
            continue
        if not isinstance(raw.get("subject"), str) or not raw["subject"].strip():
            warnings.append(f"{where}: missing subject, dropped")
            continue

        explicitness, ok = _score(raw.get("explicitness"), 1.0)
        if not ok:
            warnings.append(f"{where}: explicitness {raw.get('explicitness')!r} -> default 1.0")
        confidence, ok = _score(raw.get("confidence"), 0.5)
        if not ok:
            warnings.append(f"{where}: confidence {raw.get('confidence')!r} -> default 0.5")

        value = _normalize_value(raw.get("value", ""), msg)
        supersedes, warn = _validate_supersedes(raw.get("supersedes"), active_ids)
        if warn:
            warnings.append(f"{where}: {warn}")

        drafts.append(
            {
                "subject": raw["subject"].strip(),
                "predicate": raw["predicate"],
                "value": value,
                "valid_from": valid_from,
                "quote": quote,
                "author": str(raw.get("author") or msg["author"]),
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="hydraclaim.extract")
    parser.add_argument("scenario", help="scenario JSON file (sessions + entities)")
    parser.add_argument("--emit", metavar="OUT_JSON", help="write drafts to this file")
    args = parser.parse_args()

    doc = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
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
        print(f"{session['session_id']}: {len(drafts)} claim(s), {len(warnings)} warning(s)")

    if args.emit:
        out = {"scenario_id": scen, "drafts": all_drafts}
        Path(args.emit).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")
        print(f"wrote {len(all_drafts)} draft(s) to {args.emit}")


if __name__ == "__main__":
    main()
