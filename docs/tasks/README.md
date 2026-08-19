# Execution tasks for HydraClaim (Hack Hydra, due Aug 20 11:59 PM PT)

Each `T*.md` file here is a self-contained work order for an executing agent.
Read this file first, then the task file, then the code it references.

## Global rules — every task, no exceptions

1. **Work on a branch**: `git checkout -b task/<name>` from `main`. Commit there
   with conventional messages (`feat:`, `test:`, `docs:`). Never commit to
   `main`, never push, never force anything. A reviewer merges.
2. **Tests must stay green**: run `python -m pytest tests/ -q` from the repo
   root before every commit. The suite is currently 62 tests, all offline.
   Your new tests must also be offline: no network, no Docker, no live
   HydraDB, no LLM calls (inject fakes, like `tests/test_router.py` does with
   `llm_fn`).
3. **No new dependencies.** Everything uses `httpx` + `pytest` only. LLM
   access goes through `hydraclaim/llm.py`; graph access through
   `hydraclaim/db.py`; Cypher strings through `to_cypher_literal`.
4. **Style**: `from __future__ import annotations`, module docstrings, type
   hints, small pure functions separated from IO (see `reconcile.py` for the
   pattern). Match the tone of neighboring files.
5. **Do not modify existing files** unless the task spec lists the file and
   the exact change. If a spec's assumption turns out to be wrong (e.g. a
   function signature changed), STOP: implement nothing speculative, note the
   mismatch in your final report, and let the reviewer decide.
6. Environment: Windows + Git Bash, Python 3.11.9. Paths below are relative
   to the repo root (`C:/Repo/hydraclaim`).

## Project orientation (5 minutes)

- `PLAN.md` — the whole build plan, including benchmark arms and demo beats.
- `README.md` — quickstart and repo map.
- The flow: `generate` (synthetic sessions + ground truth) → `extract`
  (LLM claims) / `ingest` (oracle claims) → `reconcile` (supersede/contradict
  rules) → HydraDB → `probe` + `router` + `retrieve` (two-stage routing:
  FAST / DEEP / ABSTAIN) → `ask.py` (CLI).

## Tasks

| File | Scope | Needs live HydraDB to fully verify? |
|------|-------|-------------------------------------|
| `T1-benchmark-harness.md` | D4: ablation arms + metrics runner | yes (unit tests must pass without it) |
| `T2-longmemeval-loader.md` | D4: LongMemEval → scenario-doc converter | no |
| `T3-demo-polish.md` | D5: demo script, REPL, README results section | partially |
