# HydraClaim core final-fix report

Date: 2026-08-20
Branch: `feat/hydraclaim-release`

## Changes

### Bounded claim reads

Files: `hydraclaim/claim_read.py`, `hydraclaim/schema.py`,
`hydraclaim/schema.cypher`, `tests/test_claim_read.py`,
`tests/test_retrieve.py`, and `tests/test_schema.py`.

- Relation reads use one property-scoped source claim per query.
- Relation and chain reads contain no `IN` list, comma pattern, or multiple
  `MATCH` clause.
- Relation reads use one-edge queries. Each source and target claim passes a
  bounded one-hop `ABOUT` check for the selected subject and predicate.
- Chain reads use bounded iterative one-hop `SUPERSEDES` queries. The starting
  claim and each discovered older claim pass a bounded one-hop `ABOUT` check.
- Chain traversal stops at depth five and tracks seen claim identifiers for
  cycle safety.
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
- Claim-read limit failures from `/ask` and `/graph` return HTTP 409 with
  `claim_limit_exceeded`. Logs include the endpoint, subject or question
  length, limit, exception type, and full traceback. Logs do not include source
  text.
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
| `python -m pytest -q` | **224 passed** in 1.28 s |
| `ruff check --fix .` | **All checks passed** |
| `ruff format .` | **52 files left unchanged** on the final check |
| `ruff format --check .` | **52 files already formatted** |
| `git diff --check` | No whitespace errors |

Focused runs also passed: claim-read and serve tests (39); retrieval tests
(12); and schema tests (6).

## Live HydraDB evidence

The required startup command completed successfully:

```text
bash scripts/dev-up.sh
created hydradb-data/auth-token (development token only)
Container hydraclaim-release-hydradb-1 Started
waiting for HydraDB readiness — ready
HTTP query API: http://127.0.0.1:8443
```

The required live command also completed successfully:

```text
python -m hydraclaim.schema --verify
probe run id: b703cfe9
PASS  one-hop CREATE + read back  (1 row(s) back)
PASS  property-scoped selected relation reads  (1 row(s) back)
PASS  upsert by integer id (re-CREATE is idempotent)  (1 row(s) back)
PASS  string equality in WHERE over an edge pattern  (2 row(s) back)
PASS  bounded variable-length path (SUPERSEDES*1..5 shape)  (2 row(s) back)
PASS  OPTIONAL MATCH  (1 row(s) back)
PASS  aggregation count(*) over an edge pattern  (1 row(s) back)
PASS  SET update  (1 row(s) back)
PASS  label/property-scoped DETACH DELETE (reset pattern)  (0 row(s) back)
cleanup: probe nodes deleted
```

## Commits

- `8d7176f` — `fix: constrain claim reads to verified query forms`
- `50b6932` — `fix: reject invalid claim payloads before graph writes`
- `4261312` — `fix: map remote and malformed HTTP failures`
- `d516ef0` — `chore: clean reported Ruff and format findings`
- `ccc3cf8` — `fix: validate deadline claim values`
- `a562797` — `test: cover malformed classifier and suggestion responses`
- Task 7 atomic commit — claim scope and HTTP limit correctness.

## Concerns

The local HydraDB container remains running after verification. Stop it with
`docker compose down` when it is no longer needed.
