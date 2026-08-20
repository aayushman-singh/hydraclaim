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

## Round 1 fix report

### Findings addressed

- Failure logs now contain explicit `step=`, `scenario=`, `state=`,
  `exception_type=`, and safe `input=` key/value fields plus the full
  traceback from `logger.exception`.
- State includes safe progress counters for raw extraction, Slack message and
  session processing, and preformatted active reads, drafts, plans, and graph
  writes. It never contains source text or document values.
- Raw text, preformatted documents, Slack dictionary input, Slack bare-list
  input, and both `serve.dispatch` ingestion routes have regression tests for
  stable status, error code, and concise error message.
- Preformatted ingestion uses a typed step hook. Failures identify
  `validation`, `read_active`, `extract`, `reconcile`, or `graph_write`.
- Bare Slack lists log `input_id=slack-list` and the safe message count.

### Exact verification

- Initial round-1 regression run: 4 expected failures and 2 route tests passed.
  Failures showed missing state fields, opaque `step=pipeline`, and missing
  bare-list summary fields.
- `python -m pytest tests/test_ingest_api.py tests/test_serve.py -v`: 30
  passed.
- `python -m pytest tests/ -q`: 187 passed.
- `ruff check hydraclaim/ingest_api.py hydraclaim/pipeline.py
  hydraclaim/serve.py tests/test_ingest_api.py tests/test_serve.py`: passed.
- `ruff format --check hydraclaim/ingest_api.py hydraclaim/pipeline.py
  hydraclaim/serve.py tests/test_ingest_api.py tests/test_serve.py`: 5 files
  already formatted.
- `ruff check --fix .`: exit 1 only for the two known `F841` errors in
  `demo/term-video.py` (`max_w` and `total_frames`). Four unrelated Ruff
  edits were applied and restored.
- `ruff format .`: completed. Unrelated formatter edits were applied and
  restored.
- `git diff --check`: passed.
