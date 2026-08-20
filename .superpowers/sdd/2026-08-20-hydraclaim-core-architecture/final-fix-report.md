# HydraClaim core final-fix report

Date: 2026-08-20
Branch: `feat/hydraclaim-release`

## Changes

### Bounded claim reads

Files: `hydraclaim/claim_read.py`, `hydraclaim/schema.py`,
`hydraclaim/schema.cypher`, `tests/test_claim_read.py`,
`tests/test_retrieve.py`, and `tests/test_schema.py`.

- Relation reads use one property-scoped source claim per query.
- Relation reads contain no `IN` list, comma pattern, or multiple `MATCH` clause.
- Subject and predicate scope stays in the query. The target claim identifier is
  checked against the selected endpoint set before the row is returned.
- Chain reads use one bounded variable-length path with a property-scoped start
  claim.
- Claim reads issue `LIMIT limit + 1` and raise `ClaimReadLimitError` when more
  rows exist. They do not silently truncate.
- The schema battery includes the exact selected-relation query shapes and tests
  reject the removed syntax.

### HTTP failures and suggestion mode

Files: `hydraclaim/router.py`, `hydraclaim/serve.py`, and `tests/test_serve.py`.

- Classifier response-shape failures use `ClassificationError`.
- LLM, classifier, graph, and suggestion failures return stable HTTP codes and
  messages.
- Array and null request or classifier response shapes have focused tests.
- Remote failures use `logger.exception` with endpoint, mode, body type, field
  names, question length, exception type, and traceback context.
- Heuristic and LLM suggestion modes are explicit. LLM suggestion errors do not
  select the heuristic result.
- No new broad exception or `BaseException` handler hides programming errors.

### Strict extraction and graph preflight

Files: `hydraclaim/extract.py`, `hydraclaim/graph_write.py`,
`tests/test_extract.py`, and `tests/test_graph_write.py`.

- Invalid or missing confidence and explicitness values raise. No score default
  or clamping remains in extraction.
- GraphWriter validates the complete document or plan before the first write.
- Validation rejects invalid calendar dates, invalid deadline values, empty
  subject, value, quote, or author, invalid status, invalid score ranges, and
  unsupported property names or types.
- Recording tests confirm zero writes for each invalid input class.

### Ruff and formatting cleanup

Files in commit `d516ef0` contain only the reported unused imports, unused
variables, and Ruff formatting changes. Demo behavior is not refactored.

## Verification

| Command | Result |
| --- | --- |
| `python -m pytest -q` | **214 passed** in 1.37 s |
| `ruff check --fix .` | **All checks passed** |
| `ruff format .` | **51 files left unchanged** |
| `ruff format --check .` | **51 files already formatted** |
| `git diff --check` | No whitespace errors |

Focused runs also passed: claim reads, retrieval, and schema tests (27); graph
preflight tests (21); serve tests (23); router and serve tests (38); and
extraction tests (15).

## Live HydraDB evidence

Readiness was checked before the query rewrite:

- `docker compose ps` showed no running HydraDB container.
- `http://127.0.0.1:9090/readyz` refused the connection.
- `http://127.0.0.1:8443` refused the connection.

The required live command was run:

```text
python -m hydraclaim.schema --verify
```

It exited with status 1. The first query raised
`httpx.ConnectError: [WinError 10061] No connection could be made because the
target machine actively refused it`. No schema query form is claimed as live
verified in this worktree.

## Commits

- `8d7176f` — `fix: constrain claim reads to verified query forms`
- `50b6932` — `fix: reject invalid claim payloads before graph writes`
- `4261312` — `fix: map remote and malformed HTTP failures`
- `d516ef0` — `chore: clean reported Ruff and format findings`
- `ccc3cf8` — `fix: validate deadline claim values`
- `a562797` — `test: cover malformed classifier and suggestion responses`

## Concerns

Live HydraDB verification remains blocked by the unavailable local service.
Run `python -m hydraclaim.schema --verify` again against a reachable HydraDB
before release and record the PASS/FAIL result for each selected relation form.
