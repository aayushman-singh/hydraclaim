"""LongMemEval → TrustGraph scenario converter.

Converts LongMemEval question instances into the scenario-document shape produced
by `trustgraph.generate`, so the existing pipeline, retrieval, and benchmark
code can run on real long-memory benchmark data without modification.

This module does not download the LongMemEval dataset and does not ingest
anything into HydraDB.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

QTYPE_MAP = {
    "multi-session": "multi_session",
    "temporal-reasoning": "temporal",
    "knowledge-update": "knowledge_update",
    "abstention": "abstention",
}


def _parse_date(value: str) -> date:
    """Accept ``YYYY/MM/DD``, ``YYYY-MM-DD``, and datetime strings like
    ``2023-05-25 (Thu) 14:48``."""
    value = value.strip()
    # Take the first YYYY-MM-DD or YYYY/MM/DD fragment.
    m = re.search(r"\d{4}[/-]\d{2}[/-]\d{2}", value)
    if not m:
        raise ValueError(f"cannot parse date from {value!r}")
    return date.fromisoformat(m.group(0).replace("/", "-"))


def _map_qtype(question_type: str) -> str:
    """Map a LongMemEval question_type to a TrustGraph QUESTION_TYPES value."""
    return QTYPE_MAP.get(question_type, "lookup")


def convert_instance(instance: dict) -> dict:
    """Convert one LongMemEval instance to a TrustGraph scenario document."""
    qid = instance["question_id"]
    session_ids = instance.get("haystack_session_ids", [])
    session_dates = instance.get("haystack_dates", [])
    session_turns = instance.get("haystack_sessions", [])

    if not (len(session_ids) == len(session_dates) == len(session_turns)):
        raise ValueError(
            f"question {qid!r}: haystack_session_ids, haystack_dates, and "
            f"haystack_sessions must have the same length"
        )

    sessions: list[dict] = []
    for sid, date_str, turns in zip(session_ids, session_dates, session_turns):
        started = datetime.combine(
            _parse_date(date_str), time(9, 0), tzinfo=timezone.utc
        )
        messages: list[dict] = []
        for i, turn in enumerate(turns):
            ts = started + timedelta(minutes=2 * i)
            messages.append(
                {
                    "msg_id": f"{sid}-m{i:03d}",
                    "ts": ts.isoformat(),
                    "author": turn["role"],
                    "source_kind": "chat",
                    "channel": "longmemeval",
                    "text": turn["content"],
                }
            )
        sessions.append(
            {
                "session_id": sid,
                "started_at": started.isoformat(),
                "messages": messages,
            }
        )

    qtype = _map_qtype(instance.get("question_type", ""))
    if qid.endswith("_abs"):
        qtype = "abstention"
    qa = {
        "question": instance["question"],
        "answer": instance["answer"],
        "qtype": qtype,
        "gold_claim_keys": [],
    }

    return {
        "scenario_id": f"lme_{qid}",
        "description": instance.get("question_type", ""),
        "entities": [],
        "sessions": sessions,
        "ground_truth": {"claims": [], "qa": [qa]},
    }


def estimate_tokens(doc: dict) -> int:
    """Rough token estimate: total message text length divided by four."""
    total = 0
    for session in doc.get("sessions", []):
        for msg in session.get("messages", []):
            total += len(msg.get("text", ""))
    return total // 4


def sample_instances(
    instances: list[dict], n: int, seed: int, per_type_cap: int
) -> list[dict]:
    """Deterministic stratified sample by mapped TrustGraph question type.

    For each type (in sorted order), the instances are shuffled with
    ``random.Random(seed)`` and up to ``per_type_cap`` are selected.  If the
    total is below ``n``, remaining slots are filled from the leftovers in the
    same sorted type order.
    """
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = {}
    for inst in instances:
        qtype = _map_qtype(inst.get("question_type", ""))
        if inst.get("question_id", "").endswith("_abs"):
            qtype = "abstention"
        groups.setdefault(qtype, []).append(inst)

    selected: list[dict] = []
    leftovers: list[dict] = []
    for qtype in sorted(groups):
        group = list(groups[qtype])
        rng.shuffle(group)
        cap = min(per_type_cap, len(group))
        selected.extend(group[:cap])
        leftovers.extend(group[cap:])

    if len(selected) > n:
        return selected[:n]

    need = n - len(selected)
    if need:
        selected.extend(leftovers[:need])
    return selected


def _convert_command(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(
            f"expected JSON array of LongMemEval instances, got {type(raw).__name__}"
        )

    sampled = sample_instances(raw, args.n, args.seed, args.per_type_cap)

    counts: dict[str, int] = {}
    skipped = 0
    for inst in sampled:
        doc = convert_instance(inst)
        tokens = estimate_tokens(doc)
        if tokens > args.max_history_tokens:
            skipped += 1
            print(
                f"WARNING: skipping {doc['scenario_id']} "
                f"({tokens} tokens > {args.max_history_tokens} cap)"
            )
            continue

        qtype = doc["ground_truth"]["qa"][0]["qtype"]
        counts[qtype] = counts.get(qtype, 0) + 1
        out_path = out_dir / f"{doc['scenario_id']}.json"
        out_path.write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print(f"Wrote {sum(counts.values())} scenarios to {out_dir}")
    if skipped:
        print(f"Skipped {skipped} instances over token cap")
    print("By qtype:")
    for qtype in sorted(counts):
        print(f"  {qtype}: {counts[qtype]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="trustgraph.longmemeval")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser(
        "convert", help="convert LongMemEval instances to TrustGraph scenario docs"
    )
    convert_parser.add_argument("input", help="path to a LongMemEval JSON file")
    convert_parser.add_argument("--out", required=True, help="output directory")
    convert_parser.add_argument(
        "--n", type=int, default=100, help="number of instances to sample"
    )
    convert_parser.add_argument(
        "--seed", type=int, default=42, help="random seed for deterministic sampling"
    )
    convert_parser.add_argument(
        "--per-type-cap",
        type=int,
        default=25,
        help="maximum instances to take from each question type",
    )
    convert_parser.add_argument(
        "--max-history-tokens",
        type=int,
        default=200_000,
        help="skip instances whose estimated history tokens exceed this cap",
    )
    convert_parser.set_defaults(func=_convert_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
