# Task 4 report

## Result

HydraClaim now has a Windows clean-install verifier for release `0.2.0`.

- The verifier selects only `dist/hydraclaim-0.2.0-py3-none-any.whl`.
- It stops unless exactly one matching wheel exists.
- It creates a clean `.venv-package-test` environment.
- It installs the selected wheel and its declared dependencies.
- It checks `hydraclaim --version`.
- It checks help output for all ten public commands.
- It stops with an error when any step fails.
- The README and demo guide use the installed `hydraclaim` command.
- The README keeps `python -m hydraclaim.<command>` only as a compatibility note.

## Verification

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/verify-package.ps1`: passed.
- Version check: `hydraclaim 0.2.0`.
- Help checks: `ask`, `serve`, `schema`, `generate`, `ingest`, `extract`, `evaluate`, `pipeline`, `benchmark`, and `longmemeval`.
- `python -m pytest tests/test_package_metadata.py -q`: 10 passed.
- `python -m pytest tests/ -q`: 255 passed.
- `ruff check --fix .`: passed.
- `ruff format .`: 55 files unchanged.
- `git diff --check`: passed.
- `python -m build`: built the `0.2.0` wheel and source archive.

## Files

- `scripts/verify-package.ps1`
- `README.md`
- `docs/DEMO.md`
- `tests/test_package_metadata.py`
