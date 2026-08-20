# HydraClaim CLI package final fix report

Date: 2026-08-20
Branch: `feat/hydraclaim-release`

## Status

Complete. The final CLI package findings are addressed.

## Changes

- README states that LLM classification needs `hydraclaim ask --llm`.
  `LLM_API_KEY` alone never changes the mode.
- Ask, serve, schema, ingest, extract, pipeline, and benchmark help output
  names the HydraDB or LLM settings that the command needs.
- HydraDB and LLM setting checks run before external access. Missing settings
  produce a concise nonzero CLI error.
- Package tests require exactly these files after a clean build:
  `dist/hydraclaim-0.2.0-py3-none-any.whl` and
  `dist/hydraclaim-0.2.0.tar.gz`.
- Archive tests do not skip when artifacts are missing. They reject local
  state, tests, bytecode, and exact secret file names or secret extensions.
  Source keys are not excluded by broad substring patterns.
- The package verifier removes old build output, builds both artifacts, runs
  Twine and package tests, installs the exact wheel, runs an offline fixture,
  and checks HydraDB and LLM configuration failures without remote access.
- Python 3.12 and 3.13 classifiers are present.
- Demo scripts use the installed `hydraclaim` command.
- `python -m hydraclaim.cli` now has a module entry point.
- GitHub workflows and PyPI publication files were not added.

## Commits

- `95295a7` — `fix: validate explicit CLI settings`
- `69b73ce` — `build: harden package release verification`

## Verification

| Command | Result |
| --- | --- |
| `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/verify-package.ps1` | passed; clean build, exact artifacts, Twine, package tests, clean install, help, fixture, and configuration checks |
| `python -m pytest tests/ -q` | **273 passed** |
| `ruff check --fix .` | passed |
| `ruff format .` | 56 files unchanged |
| `git diff --check` | passed |

## Concerns

The local HydraDB container is still running for other release checks. Stop it
with `docker compose down` when it is no longer needed.
