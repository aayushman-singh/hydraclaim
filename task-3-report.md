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

## Round 1 remediation

The first sdist included local database files, `auth-token`, generated sessions,
package metadata, and internal agent files. The sdist exclusion list now removes
these paths and other local state:

- HydraDB data, sessions, LongMemEval data, results, and egg-info.
- `.env` files, caches, virtual environments, worktrees, build, and dist.
- `.superpowers`, `.claude`, `.playwright-mcp`, and internal agent documents.
- Generated demo video, image, thumbnail, and output files.

The rebuilt archives contain:

- Wheel: 37 entries. Required CLI and schema files are present. Forbidden entries: none.
- Source archive: 75 entries. Required CLI, schema, and metadata files are present. Forbidden entries: none.
- Intended `data/samples`, source, useful documentation, demo scripts, and web assets remain present.

Round 1 verification:

- `python -m pytest tests/test_package_metadata.py -v`: 8 passed.
- `python -m build`: built `hydraclaim-0.2.0-py3-none-any.whl` and `hydraclaim-0.2.0.tar.gz`.
- `python -m twine check ...`: both artifacts passed.
- Both archive types were opened and checked for secrets, auth tokens, environment files, database data, tests, caches, internal agent workspace, generated sessions/results, egg-info, and generated demo files.

Final fix verification:

- `python -m pytest`: 253 passed.
- `ruff check --fix .`: passed.
- `ruff format --check .`: 55 files already formatted.
- `git diff --check`: passed.
- Archive inspection: wheel 37 entries and sdist 75 entries. Both have no forbidden entries.
