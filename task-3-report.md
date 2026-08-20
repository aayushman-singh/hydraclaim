# Task 3 report

## Result

HydraClaim package metadata is ready for release `0.2.0`.

- `pyproject.toml` is the only version source.
- The package uses Hatchling with explicit `hydraclaim` package inclusion.
- The package has no duplicate `__version__` value.
- Build artifacts and the package test environment are ignored.
- Source archives exclude tests and build artifacts.

## Verification

- `python -m pytest`: 252 passed.
- `ruff check --fix .`: passed.
- `ruff format --check .`: 55 files formatted.
- `git diff --check`: passed.
- `python -m build`: built one wheel and one source archive.
- `python -m twine check dist/hydraclaim-0.2.0-py3-none-any.whl dist/hydraclaim-0.2.0.tar.gz`: both passed.
- Archive inspection: required CLI and schema files are present. Tests, bytecode, build, and dist files are absent.

## Artifacts

- `dist/hydraclaim-0.2.0-py3-none-any.whl`
- `dist/hydraclaim-0.2.0.tar.gz`
