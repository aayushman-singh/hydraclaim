# Whole-branch final fix report

Date: 2026-08-21  
Branch: `feat/hydraclaim-release`  
Status: complete

## Commits

The final fix range is `aea4e4d^..2380b01`:

- `aea4e4d` — `fix: enforce graph integrity and idempotent writes`
- `d721c6d` — `fix: bound temporal claim reads`
- `314657c` — `fix: reject malformed extraction and adapter failures`
- `fe8fd5d` — `fix: map API and CLI boundary failures`
- `c84dad1` — `ci: pin release actions and verify clean wheels`
- `2380b01` — `docs: document write authentication and read behavior`

## Finding coverage

1. Supersession writes reject self, cross-subject, cross-predicate, and cycle
   edges before writes. Existing reachable edges are included. Chain depth and
   chain reads are iterative and raise `GraphIntegrityError` on a cycle.
2. `GraphWriter` inspects and completes each claim, evidence, source, and
   provenance edge. A failed operation raises. A later explicit retry repairs
   the missing graph parts. No silent retry is added.
3. Extraction rejects malformed roots and claim fields. It does not drop
   records, stringify values, or replace authors. Malformed pipeline input
   produces zero writes.
4. HydraDB and language-model adapters wrap transport, JSON, and response-shape
   failures in typed errors. HTTP responses use stable codes and log tracebacks.
5. POST handlers require a decimal, non-negative, capped `Content-Length` and
   reject invalid lengths before reading the body.
6. Installed CLI dispatch maps configuration, validation, file, HydraDB, LLM,
   graph-integrity, and JSON failures. It logs tracebacks and leaves unexpected
   programming errors uncaught.
7. `as_of` filters claim, membership, relation, chain, origin, and temporal
   reads. Future-claim regressions are covered.
8. `retrieve.fetch_chain(db, claim_id)` remains supported. Omitted subjects use
   one bounded scope lookup and fail when the scope is not unique.
9. Relation and one-hop chain queries use `LIMIT limit + 1` and raise
   `ClaimReadLimitError` on overflow. HTTP and CLI mappings are covered.
10. Write routes fail closed without `HYDRACLAIM_WRITE_KEY` and require its
    exact bearer value. README and local procedures show the local key.
11. README and examples describe empty-string open validity, iterative reads,
    and individual writes without `UNWIND`.
12. GitHub and PyPI actions use immutable verified commit SHAs. Workflow tests
    parse YAML. Linux CI runs the clean-wheel verifier through `pwsh` and fails
    clearly when `pwsh` is unavailable.
13. LLM suggestion mode returns only the LLM result. A valid empty list remains
    empty. LLM failure does not select the heuristic result.
14. Chain citations include the head and every ancestor value in deterministic
    order.
15. Package tests require exactly one wheel and one source archive. The package
    verifier reads runtime dependencies from `requirements.txt`.

## Verification

| Check | Outcome |
| --- | --- |
| `ruff check --fix .` | passed |
| `ruff format .` | passed; final tree clean |
| `python -m pytest -q -m "not artifact"` | 345 passed, 1 deselected |
| clean `python -m build` | wheel and source archive built |
| `python -m twine check dist/*` | both artifacts passed |
| `python -m pytest -q -m artifact` | 1 passed, 345 deselected |
| `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/verify-package.ps1` | passed; clean build, Twine, 15 package tests, clean install, CLI help, fixtures, and configuration checks |
| `python -m hydraclaim.schema --verify` | all 9 live HydraDB probes passed and cleanup succeeded |
| YAML parsing for both workflows | passed |
| `git diff --check` | passed |
| final `git status --short` | clean |

## Action tag and SHA evidence

The immutable pins were checked with `git ls-remote`:

- `actions/checkout` `v7` -> `3d3c42e5aac5ba805825da76410c181273ba90b1`
- `actions/setup-python` `v7` -> `5fda3b95a4ea91299a34e894583c3862153e4b97`
- `actions/upload-artifact` `v7` -> `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`
- `actions/download-artifact` `v8` -> `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c`
- `pypa/gh-action-pypi-publish` `v1.12.4` tag object ->
  `7f25271a4aa483500f742f9492b2ab5648d61011`
- `pypa/gh-action-pypi-publish` `v1.12.4` peeled commit ->
  `76f52bc884231f62b9a034ebfe128415bbaabdfc` (workflow pin)

## Concerns

The live battery used the local HydraDB service at `127.0.0.1:8443`; the
service remains available for local work. Linux CI requires `pwsh` for the
clean-wheel acceptance step and fails explicitly if it is not installed.
