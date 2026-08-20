# Task 6 report: context-rich ingest failures

## Status

Complete.

## Behaviour implemented

- Ingest failures now log the failed step, scenario identifier, safe request
  metadata, exception type, and full traceback.
- Request summaries contain field names and collection or text lengths only.
  They do not contain secrets or source document contents.
- Raw and Slack ingestion stop at the failed operation and return
  `{"code": "ingest_failed", "error": "ingestion failed"}` with status 500.
- Missing language-model configuration returns a stable 503 error code.
- Ingest validation and server error responses use stable error codes with
  concise messages.
- Ingestion dependencies are module-level seams so failed steps can be tested
  directly.

## TDD evidence

- The new extraction failure test first failed because the handler returned a
  raw exception string and did not log traceback context.
- The test passes after the step-local exception log and stable response were
  added.

## Verification

- `python -m pytest tests/test_ingest_api.py tests/test_serve.py -v`: 21 passed.
- `python -m pytest tests/ -q`: 178 passed.
- Ruff checks for all changed files: passed.
- Ruff formatting check for all changed files: passed.
- `git diff --check`: passed.
- `ruff check --fix .`: remains non-zero for two existing unused-variable
  errors in `demo/term-video.py` (`max_w` and `total_frames`). Unrelated Ruff
  edits were restored.
- `ruff format .`: completed. Unrelated formatter edits were restored.

## Commit

Commit is recorded in the final task handoff.

## Concerns

- Repository-wide Ruff remains non-zero until the two existing unused-variable
  errors in `demo/term-video.py` are fixed in a separate change.
