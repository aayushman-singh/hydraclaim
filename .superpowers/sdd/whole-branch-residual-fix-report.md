# Whole-branch residual fix report

Date: 2026-08-21  
Branch: `feat/hydraclaim-release`  
Status: complete

## Commits

- `6e4afe7` — `fix: enforce contradiction relation scope`
- `5cf1613` — `fix: allow shared supersession ancestors`
- `85612e9` — `fix: detect branched supersession cycles`
- `1f72145` — `fix: cite both temporal claims`
- `417a6ae` — `style: format residual graph modules`
- `a0a9c1c` — `fix: validate pipeline input documents`
- `db7a204` — `test: require one wheel metadata file`
- `f1b785a` — `fix: reject duplicate suggestion routes`

## Finding coverage

1. `CONTRADICTS` now joins two distinct Claim nodes with the same subject and
   predicate. Ingest and reconciliation writes reject self, cross-subject, and
   cross-predicate relations before the first write.
2. Chain reads collect the selected directed edges. They reject a true cycle,
   including a cycle hidden behind a branch, and accept one shared ancestor
   reached from two valid branches.
3. Temporal answers cite the current claim and the previous claim in stable
   order.
4. Pipeline documents are validated before any field access or HydraDB
   connection. Malformed roots, entities, sessions, and messages raise the
   typed `PipelineInputError`. The unified CLI maps it to concise validation
   output and a non-zero exit.
5. Artifact tests require exactly one wheel metadata file before reading it.
6. LLM suggestion responses reject duplicate routes with an explicit error.

## Verification

| Check | Outcome |
| --- | --- |
| Focused residual tests | passed |
| `python -m pytest -q -m "not artifact"` | 364 passed, 1 deselected |
| `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/verify-package.ps1` | passed; clean build, Twine, package tests, clean install, CLI checks, fixture checks, and configuration checks |
| `python -m pytest -q -m artifact` | 1 passed, 364 deselected |
| `ruff check --fix .` | passed |
| `ruff format .` | passed; no changes |
| `git diff --check` | passed |
| final `git status --short` | clean |
