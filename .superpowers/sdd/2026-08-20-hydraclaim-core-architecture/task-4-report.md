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

## Round 1 fix report

### Findings addressed

- Claim, entity, relation, chain, graph, and citation reads now use explicit
  deterministic ordering with stable key or identifier tie-breakers.
- Equal-timestamp active selection, temporal output, conflict citation order,
  and supersession chain order have regression coverage.
- Supersession chain reads constrain both the starting claim and every older
  claim to the supplied subject and optional predicate.
- Relation reads filter returned endpoints against the selected claim IDs.
- `AnswerResult.classification` uses a type-checking-only `Classification`
  import and has no runtime router import cycle.

### Exact verification

- `python -m pytest tests/test_claim_read.py -q` after new tests and before the
  fix: 2 passed, 5 failed as expected for missing ordering and scope behavior.
- `python -m pytest tests/test_claim_read.py tests/test_retrieve.py
  tests/test_serve.py tests/test_benchmark.py -q`: 48 passed.
- `python -m pytest -q`: 171 passed.
- Focused Ruff check for implementation files and read tests: passed.
- `ruff format --check` for implementation files and read tests: 6 files
  already formatted.
- `ruff check --fix .`: exit 1 only for existing `F841` errors in
  `demo/term-video.py` (`max_w` and `total_frames`); four unrelated fixes were
  restored.
- `git diff --check`: passed.

### Round 1 commit

Commit is recorded in the final task handoff.
