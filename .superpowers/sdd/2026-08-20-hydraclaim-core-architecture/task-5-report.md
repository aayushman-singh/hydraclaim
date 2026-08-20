# Task 5 report

## Status

Complete.

## Changes

- Renamed all temporary verification nodes from `TGProbe` to `HydraClaimProbe`.
- Updated the schema document to use empty-string validity windows.
- Updated the schema document to use directed `CONTRADICTS` matches.
- Removed unsupported path-length syntax and documented client-side chain-depth calculation.
- Made cleanup failure set schema verification to false.
- Added schema drift and cleanup failure tests.

## Verification

- Focused tests: `20 passed`.
- Full tests: `174 passed`.
- Touched-file Ruff check: passed.
- Required `ruff check --fix .`: reports two pre-existing unused variables in `demo/term-video.py` after four automatic fixes. The unrelated formatter changes were removed.
- Required `ruff format .`: completed. Formatter-only changes to unrelated files were removed.

## Concerns

The repository-wide Ruff check remains non-zero because of the existing `max_w` and `total_frames` unused variables in `demo/term-video.py`. They are outside Task 5 and were not changed.

## Review fix round 1

- Changed the provenance example to match the string `key` property. Claim graph `id` values remain integers.
- Added `render_schema_reference()` as the canonical schema source in `hydraclaim/schema.py`.
- Added a test that requires `schema.cypher` to equal the rendered reference.
- Added a regression test for the provenance `key` match.
- Focused tests after the fix: `22 passed`.
- Full tests after the fix: `176 passed`.
- Touched-file Ruff check after the fix: passed.
