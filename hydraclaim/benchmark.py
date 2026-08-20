"""Benchmark harness for the HydraClaim routing story.

Runs four ablation arms over scenario ground-truth QA and reports accuracy,
abstention quality, latency, and retrieval cost per question.

    python -m hydraclaim.benchmark data/sessions/*.json --arm all
"""

from __future__ import annotations

import argparse
import glob
import json
import string
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hydraclaim.claim_read import ClaimReader, ClaimScope
from hydraclaim.db import HydraDB
from hydraclaim.retrieve import fetch_entities
from hydraclaim.router import ROUTE_DEEP, ROUTE_FAST, classify

ROUTE_NAIVE_RAG = "NAIVE_RAG"


def _tokenize(text: str) -> set[str]:
    return set(_normalize(text).split())


def naive_answer(db: CountingDB, question: str, roster: list[dict]) -> dict:
    """Flat-RAG baseline: score claims by token overlap with the question.

    Does not use SUPERSEDES/CONTRADICTS edges or predicate coverage — it just
    returns the most word-overlapping active claim. This is the 're-derive
    everything from retrieved chunks' story that HydraClaim avoids.
    """
    cls = classify(question, roster)
    if cls.subject is None:
        return {
            "route": ROUTE_NAIVE_RAG,
            "answer": (
                f"I don't have any recorded information about '{question}'. "
                f"No subject matched the entity roster."
            ),
            "citations": [],
        }

    rows = [
        {
            "predicate": claim.predicate,
            "value": claim.value,
            "status": claim.status,
            "valid_from": claim.valid_from,
        }
        for claim in ClaimReader(db).read_claims(
            ClaimScope(subject=cls.subject, active_only=True)
        )
    ]
    active = [r for r in rows if r.get("status") == "active"]
    if not active:
        return {
            "route": ROUTE_NAIVE_RAG,
            "answer": (
                f"I don't have any recorded information about the "
                f"{cls.predicate or 'facts'} of '{cls.subject}'. "
                f"No active claims found."
            ),
            "citations": [],
        }

    q_tokens = _tokenize(question)
    if cls.predicate:
        q_tokens.add(cls.predicate)

    def score(row: dict) -> float:
        text = f"{row.get('predicate', '')} {row.get('value', '')}"
        tokens = _tokenize(text)
        if not tokens:
            return 0.0
        return len(tokens & q_tokens) / len(tokens)

    best = max(active, key=score)
    return {
        "route": ROUTE_NAIVE_RAG,
        "answer": f"{cls.subject} — {best['predicate']}: {best['value']}.",
        "citations": [],
    }


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation at token edges."""
    tokens = text.lower().split()
    tokens = [tok.strip(string.punctuation) for tok in tokens]
    return " ".join(tok for tok in tokens if tok)


class CountingDB:
    """Wraps a HydraDB and counts read queries."""

    def __init__(self, db: HydraDB) -> None:
        self._db = db
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def reset(self) -> None:
        self._count = 0

    def query(self, cypher: str, consistency: str = "causal") -> list[dict[str, Any]]:
        self._count += 1
        return self._db.query(cypher, consistency=consistency)

    def query_one(
        self, cypher: str, consistency: str = "causal"
    ) -> dict[str, Any] | None:
        self._count += 1
        return self._db.query_one(cypher, consistency=consistency)

    def node_exists(self, label: str, node_id: str) -> bool:
        self._count += 1
        return self._db.node_exists(label, node_id)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "CountingDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def router_only_route(question_type: str) -> str:
    """No-probe routing: lookups go FAST, everything else goes DEEP."""
    return ROUTE_FAST if question_type == "lookup" else ROUTE_DEEP


def correct(
    result: dict, gold_answer: str, qtype: str, rubric: list[str] | None = None
) -> bool:
    """Return True when the produced answer matches the gold answer.

    - Abstention questions are correct only when the system abstained.
    - Questions with a rubric are correct when every rubric item appears in
      the produced answer (rubric = required content, for long-form gold
      answers where verbatim substring matching is impossible).
    - Other questions are correct when the normalized gold answer appears as a
      substring of the normalized produced answer.
    """
    if qtype == "abstention":
        return result["route"] == "ABSTAIN"

    produced = _normalize(result["answer"])
    if rubric:
        return all(_normalize(item) in produced for item in rubric)
    gold = _normalize(gold_answer)
    return gold in produced


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def run_arm(
    db: CountingDB,
    scenarios: list[dict],
    arm: str,
    judge=None,
) -> dict:
    """Run one benchmark arm over the scenario QA pairs.

    `judge` is a callable (result, gold_answer, qtype, rubric) -> bool;
    defaults to `correct`.
    """
    judge = judge or correct
    per_qtype: dict[str, dict[str, int]] = {}
    abstention = {"tp": 0, "fp": 0, "fn": 0}
    latency_ms: list[float] = []
    queries_per_question: list[int] = []
    questions = 0

    roster: list[dict] = []
    if arm in ("router-only", "naive-rag"):
        roster = fetch_entities(db)

    for scenario in scenarios:
        for qa in scenario.get("ground_truth", {}).get("qa", []):
            question = qa["question"]
            gold = qa["answer"]
            qtype = qa["qtype"]

            db.reset()
            start = time.perf_counter()

            reader = ClaimReader(db)
            if arm == "router+probe":
                result = reader.answer(question).as_dict()
            elif arm == "router-only":
                cls = classify(question, roster)
                result = reader.answer(
                    question, force_route=router_only_route(cls.question_type)
                ).as_dict()
            elif arm == "always-deep":
                result = reader.answer(question, force_route=ROUTE_DEEP).as_dict()
            elif arm == "naive-rag":
                result = naive_answer(db, question, roster)
            else:
                raise ValueError(f"unknown arm: {arm}")

            latency_ms.append((time.perf_counter() - start) * 1000)
            queries_per_question.append(db.count)
            questions += 1

            is_abstain = result["route"] == "ABSTAIN"

            if qtype == "abstention":
                if is_abstain:
                    abstention["tp"] += 1
                else:
                    abstention["fn"] += 1
            else:
                if is_abstain:
                    abstention["fp"] += 1

            metric = per_qtype.setdefault(qtype, {"n": 0, "correct": 0})
            metric["n"] += 1
            if qtype == "abstention":
                if is_abstain:
                    metric["correct"] += 1
            elif judge(result, gold, qtype, qa.get("rubric")):
                metric["correct"] += 1

    return {
        "arm": arm,
        "questions": questions,
        "per_qtype": per_qtype,
        "abstention": abstention,
        "latency_ms": latency_ms,
        "queries_per_question": queries_per_question,
    }


def summarize(arm_results: list[dict]) -> str:
    """Render arm results as a markdown table."""
    qtypes = sorted({qt for r in arm_results for qt in r["per_qtype"]})
    headers = (
        ["arm", "accuracy"]
        + qtypes
        + ["abstention P", "abstention R", "queries/q", "p50 ms", "p95 ms"]
    )

    def _fmt(value: float) -> str:
        return f"{value:.3f}"

    rows: list[list[str]] = []
    for result in arm_results:
        total_n = sum(m["n"] for m in result["per_qtype"].values())
        total_correct = sum(m["correct"] for m in result["per_qtype"].values())
        accuracy = total_correct / total_n if total_n else 0.0

        abst = result["abstention"]
        p = abst["tp"] / (abst["tp"] + abst["fp"]) if (abst["tp"] + abst["fp"]) else 0.0
        r = abst["tp"] / (abst["tp"] + abst["fn"]) if (abst["tp"] + abst["fn"]) else 0.0

        queries = result["queries_per_question"]
        mean_queries = sum(queries) / len(queries) if queries else 0.0
        lat = result["latency_ms"]
        p50 = _percentile(lat, 0.50)
        p95 = _percentile(lat, 0.95)

        row = [result["arm"], _fmt(accuracy)]
        for qt in qtypes:
            m = result["per_qtype"].get(qt, {"n": 0, "correct": 0})
            row.append(_fmt(m["correct"] / m["n"]) if m["n"] else "n/a")
        row.extend(
            [
                _fmt(p),
                _fmt(r),
                f"{mean_queries:.1f}",
                f"{p50:.1f}",
                f"{p95:.1f}",
            ]
        )
        rows.append(row)

    widths = [
        max(len(rows[i][c]) for i in range(len(rows))) for c in range(len(headers))
    ]
    widths = [max(len(headers[i]), widths[i]) for i in range(len(headers))]

    def _line(cells: list[str]) -> str:
        return (
            "| "
            + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))
            + " |"
        )

    lines = [_line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines.extend(_line(row) for row in rows)
    return "\n".join(lines)


def _expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        if "*" in pattern or "?" in pattern:
            paths.extend(Path(p) for p in glob.glob(pattern))
        else:
            paths.append(Path(pattern))
    return sorted(set(paths))


def main(argv: Sequence[str] | None = None) -> int | None:
    from hydraclaim.config import command_epilog

    parser = argparse.ArgumentParser(
        prog="hydraclaim benchmark",
        epilog=command_epilog(hydradb=True),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("scenarios", nargs="+", help="scenario JSON files or globs")
    parser.add_argument(
        "--arm",
        choices=["router+probe", "router-only", "always-deep", "naive-rag", "all"],
        default="all",
        help="benchmark arm to run",
    )
    args = parser.parse_args(argv)

    from hydraclaim import config

    try:
        config.require_settings(hydradb=True)
    except config.ConfigurationError as exc:
        parser.error(str(exc))

    scenario_paths = _expand_paths(args.scenarios)
    scenarios = [json.loads(p.read_text(encoding="utf-8")) for p in scenario_paths]

    arms = (
        ["router+probe", "router-only", "always-deep", "naive-rag"]
        if args.arm == "all"
        else [args.arm]
    )

    from hydraclaim.config import connect

    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)

    with connect() as raw_db:
        db = CountingDB(raw_db)
        arm_results = [run_arm(db, scenarios, arm) for arm in arms]

    summary = summarize(arm_results)
    print(summary)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = results_dir / f"benchmark-{timestamp}.json"
    out_path.write_text(
        json.dumps({"arm_results": arm_results, "summary": summary}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
