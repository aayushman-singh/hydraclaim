# Task 4 report: bounded claim-read module

## Status

Complete.

## Behaviour implemented

- Added frozen `ClaimScope`, `ClaimView`, `ProbeResult`, `Citation`, and
  `AnswerResult` dataclasses.
- Added `ClaimReader.read_claims` for subject, predicate, activity, time, and
  limit-scoped claim reads.
- Added `ClaimReader.answer` with explicit classification mode propagation and
  typed route, text, citations, classification, and probe output.
- Scoped every `SUPERSEDES` and `CONTRADICTS` query by the selected subject,
  optional predicate, and selected claim identifiers.
- Routed probe, retrieval, graph view, and benchmark reads through
  `ClaimReader`.
- Kept the legacy dictionary result shape in `retrieve.answer` and HTTP output.
- Benchmark abstention logic now uses the structured route field.

## TDD evidence

- `python -m pytest tests/test_claim_read.py -v` initially failed during
  collection because `hydraclaim.claim_read` did not exist.
- The new structured-result and relation-scope tests pass after implementation.

## Verification

- `python -m pytest tests/test_claim_read.py tests/test_retrieve.py
  tests/test_serve.py tests/test_benchmark.py -q`: 42 passed.
- `python -m pytest -q`: 165 passed.
- Focused Ruff check for the implementation files and read tests: passed.
- `ruff check --fix .`: exit 1 only for the two known demo `F841` errors in
  `demo/term-video.py` (`max_w` and `total_frames`). Unrelated Ruff edits were
  restored.
- `ruff format .`: completed. Unrelated formatter edits were restored.
- `git diff --check`: passed.

## Commit

Commit is recorded in the final task handoff.

## Concerns

- Repository-wide Ruff remains non-zero until the two existing unused-variable
  errors in `demo/term-video.py` are fixed in a separate change.
