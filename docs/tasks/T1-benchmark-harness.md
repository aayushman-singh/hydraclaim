# T1 — Benchmark harness (ablation arms + metrics)

Branch: `task/benchmark-harness`. Read `docs/tasks/README.md` first.

## Goal

PLAN.md defines the proof for the routing story: three arms — **Always Deep**,
**Question Router**, **Router + Graph Probe** — compared on accuracy, abstention
quality, and cost. Build the runner that produces that table.

## Create: `trustgraph/benchmark.py`

Reuse `trustgraph/retrieve.py::answer(db, question, *, force_route=...)` — it
already returns `{"route", "answer", "citations", "classification", "probe"}`.
Do not modify retrieve.py.

Public surface:

- `class CountingDB` — wraps a `HydraDB` instance, forwards `query`/
  `query_one`/`node_exists`/`close`, counts queries. Used to measure
  retrieval cost per question per arm.
- `router_only_route(question_type: str) -> str` — the no-probe arm:
  `"lookup"` → `ROUTE_FAST`, everything else → `ROUTE_DEEP`. (Question Router
  never abstains; that is exactly its weakness and the metrics should show it.)
- `correct(result: dict, gold_answer: str, qtype: str) -> bool`:
  - if `qtype == "abstention"`: correct iff `result["route"] == "ABSTAIN"`.
    (Also count "answer text contains a refusal phrase" as abstain for the
    router-only arm: `"don't have any recorded information"`.)
  - else: normalized `gold_answer` is a substring of the normalized produced
    answer. Normalization: lowercase, collapse whitespace, strip punctuation
    at token edges. Gold answers like `"2026-10-17"` or `"Priya Shah"` must
    match inside longer produced sentences.
- `run_arm(db, scenarios: list[dict], arm: str, judge=None) -> dict` — for
  each scenario doc (same shape the generator writes; QA under
  `ground_truth.qa`), ask every question with the arm's routing, and return:
  `{arm, questions, per_qtype: {qtype: {n, correct}}, abstention: {tp, fp, fn},
    latency_ms: [..], queries_per_question: [..]}`.
  - arms: `"router+probe"` (force_route=None), `"router-only"`,
    `"always-deep"` (force_route=ROUTE_DEEP).
  - abstention treated as its own class: tp = gold abstention answered with
    abstain; fp = abstained when gold has an answer; fn = answered when gold
    is abstention.
- `summarize(arm_results: list[dict]) -> str` — markdown table: one row per
  arm, columns: accuracy overall, per-qtype accuracy, abstention P/R,
  mean queries/question, p50/p95 latency. This table goes in the demo video
  and README.
- CLI: `python -m trustgraph.benchmark data/sessions/*.json --arm all`
  writes `results/benchmark-<UTC timestamp>.json` (arm results + summary)
  and prints the markdown table. `--arm router+probe|router-only|always-deep|all`.

Also create `results/` via the runner (and add `results/` to `.gitignore` —
that is an allowed edit).

## Create: `tests/test_benchmark.py` (offline)

- `correct()`: abstention gold vs ABSTAIN route / vs answered; gold substring
  matching with punctuation/case noise (`"Priya Shah."` in produced text);
  mismatch returns False.
- `router_only_route()` mapping.
- Aggregation: feed `run_arm`-shaped dicts into `summarize` (build them inline
  in the test, no db) and assert the markdown contains per-arm rows and the
  abstention P/R columns.
- `CountingDB`: wrap a stub object with a `query` method; assert counts
  increment and forwarding works.

## Acceptance

- `python -m pytest tests/ -q` green (62 existing + yours).
- `python -m trustgraph.benchmark --help` exits 0.
- `git diff main --stat` shows only: new `trustgraph/benchmark.py`,
  new `tests/test_benchmark.py`, `.gitignore` (+1 line), and this task file
  marked done below.

## Report back

Files created, test count, and a sample of the markdown table format (from
your offline test fixture, not a live run).

- [x] DONE (executing agent: check when merged-ready)
