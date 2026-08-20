"""Claim-level evaluation of extraction quality (offline).

Matching: a draft matches a gold claim when (canonical subject, predicate,
normalized value) are equal; each draft matches at most one gold claim and
vice versa. Metrics:

- precision = matched drafts / all drafts (spurious extractions count against)
- recall    = matched gold / all gold claims
- duplicates: extra drafts mapping to an already-matched gold triple
- supersession_recall: of the gold supersession links (gold claim ->
  overwritten gold claim), the fraction where both sides were matched AND
  the matched draft's `supersedes` references the matched target draft's id.
  Drafts need ids for this metric (the extract CLI assigns them); without
  ids the metric is reported as None.

CLI: python -m hydraclaim.evaluate SCENARIO_JSON DRAFTS_JSON
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hydraclaim.reconcile import canonicalize_entity, normalize_value


def load_ground_truth(scenario_doc: dict) -> list[dict]:
    return [
        {
            "key": c["key"],
            "subject": c["subject"],
            "predicate": c["predicate"],
            "value_norm": normalize_value(c["value"]),
            "valid_from": c["valid_from"],
            "supersedes": c.get("supersedes"),
        }
        for c in scenario_doc["ground_truth"]["claims"]
    ]


def evaluate(
    drafts: list[dict], scenario_doc: dict, roster: list[dict] | None = None
) -> dict:
    roster = roster if roster is not None else scenario_doc.get("entities", [])
    gold = load_ground_truth(scenario_doc)

    matched_gold: dict[str, dict] = {}  # gold key -> draft
    spurious: list[dict] = []
    duplicates = 0
    for draft in drafts:
        subject = canonicalize_entity(draft["subject"], roster)
        triple_hit = [
            g
            for g in gold
            if normalize_value(g["subject"]) == normalize_value(subject)
            and g["predicate"] == draft["predicate"]
            and g["value_norm"] == normalize_value(draft["value"])
        ]
        unmatched = [g for g in triple_hit if g["key"] not in matched_gold]
        if unmatched:
            matched_gold[unmatched[0]["key"]] = draft
        elif triple_hit:
            duplicates += 1
        else:
            spurious.append(draft)

    tp = len(matched_gold)
    fp = len(spurious)
    fn = len(gold) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    predicates = sorted(
        {g["predicate"] for g in gold} | {d["predicate"] for d in drafts}
    )
    per_predicate = {}
    for pred in predicates:
        g_tp = sum(
            1
            for k, d in matched_gold.items()
            if next(g for g in gold if g["key"] == k)["predicate"] == pred
        )
        g_fn = sum(1 for g in gold if g["predicate"] == pred) - g_tp
        g_fp = sum(1 for d in spurious if d["predicate"] == pred)
        p = g_tp / (g_tp + g_fp) if g_tp + g_fp else 0.0
        r = g_tp / (g_tp + g_fn) if g_tp + g_fn else 0.0
        per_predicate[pred] = {
            "precision": round(p, 3),
            "recall": round(r, 3),
            "tp": g_tp,
            "fp": g_fp,
            "fn": g_fn,
        }

    links = [(g["key"], g["supersedes"]) for g in gold if g["supersedes"]]
    supersession_recall = None
    if links:
        hits = 0
        for key, target in links:
            draft, target_draft = matched_gold.get(key), matched_gold.get(target)
            if (
                draft
                and target_draft
                and draft.get("supersedes") == target_draft.get("id")
            ):
                hits += 1
        supersession_recall = hits / len(links)

    return {
        "scenario": scenario_doc["scenario_id"],
        "claims_gold": len(gold),
        "claims_extracted": len(drafts),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "duplicates": duplicates,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "supersession_recall": (
            round(supersession_recall, 3) if supersession_recall is not None else None
        ),
        "per_predicate": per_predicate,
        "unmatched_gold": [
            k for k in (g["key"] for g in gold) if k not in matched_gold
        ],
        "spurious": [
            f"{d['subject']} | {d['predicate']} | {d['value']}" for d in spurious
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="hydraclaim.evaluate")
    parser.add_argument("scenario", help="scenario JSON (ground truth)")
    parser.add_argument("drafts", help="drafts JSON from hydraclaim.extract --emit")
    args = parser.parse_args()

    doc = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    drafts = json.loads(Path(args.drafts).read_text(encoding="utf-8"))["drafts"]
    report = evaluate(drafts, doc)

    print(f"scenario: {report['scenario']}")
    print(
        f"claims:   {report['claims_extracted']} extracted / {report['claims_gold']} gold"
    )
    print(
        f"precision {report['precision']:.3f}   recall {report['recall']:.3f}   "
        f"f1 {report['f1']:.3f}   duplicates {report['duplicates']}"
    )
    if report["supersession_recall"] is not None:
        print(f"supersession recall: {report['supersession_recall']:.3f}")
    print("\nper predicate:")
    for pred, m in report["per_predicate"].items():
        print(
            f"  {pred:<14} p={m['precision']:.3f} r={m['recall']:.3f} "
            f"(tp {m['tp']}, fp {m['fp']}, fn {m['fn']})"
        )
    if report["unmatched_gold"]:
        print(f"\nmissed gold claims: {', '.join(report['unmatched_gold'])}")
    if report["spurious"]:
        print("spurious drafts:")
        for line in report["spurious"]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
