# T2 — LongMemEval loader

Branch: `task/longmemeval-loader`. Read `docs/tasks/README.md` first.

## Goal

Track 3's numbers (30–40 sessions, ~115k tokens/question, knowledge updates,
abstention) mirror LongMemEval (Wu et al., arXiv:2410.10813; data:
github.com/salesforce/LongMemEval). Convert LongMemEval instances into the
same scenario-document shape our generator writes, so `pipeline`, `retrieve`,
and `benchmark` (T1) run on it unchanged. **This task is a converter only** —
do not download the dataset and do not run ingestion.

## LongMemEval instance format (verify against the repo's README if unsure)

```json
{
  "question_id": "...", "question_type": "single-session-user | multi-session |
    temporal-reasoning | knowledge-update | abstention | ...",
  "question": "...", "answer": "...",
  "question_date": "2023/05/20",
  "haystack_session_ids": ["s1", "s2"],
  "haystack_dates": ["2023/05/01", "2023/05/08"],
  "haystack_sessions": [[{"role": "user", "content": "..."},
                         {"role": "assistant", "content": "..."}], ...]
}
```

## Create: `trustgraph/longmemeval.py`

- `convert_instance(instance: dict) -> dict` — returns a scenario doc:
  - `scenario_id`: `f"lme_{question_id}"`; `description`: the question type.
  - `entities`: `[]` (no roster — extraction works without one).
  - `sessions`: per haystack session, `session_id` from `haystack_session_ids`,
    `started_at` = that session's `haystack_dates` entry parsed to
    `YYYY-MM-DDT09:00:00+00:00` (accept both `YYYY/MM/DD` and `YYYY-MM-DD`),
    messages: one per turn, `msg_id` = `f"{sid}-m{i:03d}"`, `ts` = started_at +
    2·i minutes, `author` = turn role, `source_kind` = `"chat"`,
    `channel` = `"longmemeval"`, `text` = turn content.
  - `ground_truth`: `{"claims": [], "qa": [{"question", "answer", "qtype",
    "gold_claim_keys": []}]}` with the type mapping:
    `multi-session → multi_session`, `temporal-reasoning → temporal`,
    `knowledge-update → knowledge_update`, `abstention → abstention`,
    everything else → `lookup`.
- `estimate_tokens(doc: dict) -> int` — `sum(len(message text)) // 4`.
- `sample_instances(instances, n, seed, per_type_cap) -> list[dict]` —
  deterministic stratified sample (random.Random(seed); shuffle per type,
  take up to cap, then fill remaining slots from the leftovers, types in
  sorted order).
- CLI: `python -m trustgraph.longmemeval convert INPUT.json --out data/lme/
  [--n 100] [--seed 42] [--per-type-cap 25] [--max-history-tokens 200000]`.
  Writes one scenario doc per converted instance; skips (with a printed
  warning) instances whose estimated history tokens exceed the cap. Prints a
  per-type count summary.

## Allowed edits to existing files (exactly these, nothing else)

1. `trustgraph/claims.py`: add `"chat"` to `SOURCE_KINDS`.
2. `trustgraph/scoring.py`: add `"chat": 0.5` to `KIND_DEFAULT`.
3. `README.md`: under "## Attribution", add one line that LongMemEval data
   comes from github.com/salesforce/LongMemEval (CC-BY-NC — check the repo's
   LICENSE file and state it correctly; do not copy dataset files into this
   repo).

## Create: `tests/test_longmemeval.py` (offline)

- `convert_instance` on an inline 2-session × 2-turn fixture: message shape,
  msg_id sequence, ts increments, source_kind, qa mapping (use
  `temporal-reasoning` in the fixture).
- Every `question_type` string in the mapping table maps to a valid member of
  `trustgraph.claims.QUESTION_TYPES` (import and assert membership).
- `estimate_tokens` arithmetic.
- `sample_instances`: determinism (same seed → same ids), per-type caps
  respected, refill-from-leftovers reaches n when possible.
- Date parsing: both `2023/05/20` and `2023-05-20` accepted.

## Acceptance

- `python -m pytest tests/ -q` green.
- `python -m trustgraph.longmemeval --help` exits 0.
- `git diff main --stat` shows: new `trustgraph/longmemeval.py`, new
  `tests/test_longmemeval.py`, and the three allowed edits only.

## Report back

Files created, test count, and the question_type → qtype mapping table.

- [x] DONE (executing agent: check when merged-ready)
