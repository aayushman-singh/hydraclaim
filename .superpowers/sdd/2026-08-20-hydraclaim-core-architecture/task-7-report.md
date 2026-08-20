# Task 7 report: core scope and limit correctness

## Status

Complete.

## Behaviour implemented

- `ClaimReader.read_chain` validates the starting claim with a one-hop
  `ABOUT` query.
- Every older claim from the bounded supersession path passes the same subject
  and predicate checks.
- The chain path uses one verified variable-length edge pattern.
- Relation reads use one-edge queries. They validate both endpoints with
  bounded one-hop `ABOUT` queries.
- Cross-subject and cross-predicate adapter rows do not enter a chain result,
  answer, citation, probe, or graph edge.
- `ClaimReadLimitError` from `/ask` and `/graph` returns status `409` and this
  response:

  ```json
  {"code": "claim_limit_exceeded", "error": "claim read limit exceeded"}
  ```

- Limit logs include the method, endpoint, subject or question length, limit,
  exception type, and full traceback. Logs do not include source text.
- The schema probe uses only one-edge query forms. The node-only probe create
  was removed because HydraDB accepts only edge-based `CREATE` forms.

## TDD evidence

- The new chain tests first failed because the implementation returned an
  unrelated subject and accepted an out-of-scope start claim.
- The new HTTP tests first failed because `ClaimReadLimitError` had no route
  mapping and no structured context fields.
- The relation query test first failed because the query contained an
  unsupported `ABOUT` plus relation pattern.
- The focused suite passes after the minimal query and route changes.

## Verification

- `python -m pytest -q tests/test_claim_read.py tests/test_serve.py`: **36
  passed**.
- `python -m pytest -q tests/test_retrieve.py`: **12 passed**.
- `python -m pytest -q tests/test_schema.py`: **6 passed**.
- `python -m pytest -q`: **221 passed**.
- `ruff check --fix .`: **All checks passed**.
- `ruff format .`: **4 files reformatted; 48 files left unchanged**.
- `ruff format --check .`: **52 files already formatted**.
- `git diff --check`: passed with no whitespace errors.

## Live HydraDB evidence

`bash scripts/dev-up.sh` completed with exit status 0. HydraDB became ready at
`http://127.0.0.1:8443`.

The first live schema run recorded one failure for the property-scoped
relation probe. That probe used a node-only `CREATE` and two-edge relation
matches. HydraDB rejected the group with `only one-hop edge patterns are
executable in Query engine`.

The final run of `python -m hydraclaim.schema --verify` exited with status 0:

```text
probe run id: 5398d2ac
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

## Concerns

- The local HydraDB container remains running for the next agent. Run
  `docker compose down` after work is complete.
- An untracked `dist/` directory existed before Task 7. It was not changed or
  committed.

## Commit

The changes are in one atomic conventional commit created for Task 7.
