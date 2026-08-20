# Task 2 report

## Result

HydraClaim now has a tag-only PyPI publication workflow.

- The workflow runs for `v*` tags.
- The build job checks formatting, tests, Twine metadata, and archive contents.
- The build job creates one wheel and one source archive.
- The upload job stores the exact files from `dist/`.
- The publish job downloads those files and publishes them with PyPI Trusted Publishing.
- The default permission is `contents: read`.
- Only the publish job has `id-token: write`.
- The publish job uses the `pypi` environment and the HydraClaim PyPI URL.
- No PyPI token secret is used.

## Action versions

- `actions/checkout@v7`
- `actions/setup-python@v7`
- `actions/upload-artifact@v7`
- `actions/download-artifact@v8`
- `pypa/gh-action-pypi-publish@release/v1`

## Verification

- `python -m pytest tests/test_publish_workflow.py -q`: 4 passed.
- Publish workflow YAML syntax: passed with PyYAML.
- `ruff check --fix .`: passed.
- `ruff format .`: passed.
- `python -m pytest tests/ -v`: 279 passed.
- `python -m build`: built the `0.2.0` wheel and source archive.
- `python -m twine check dist/*`: both artifacts passed.
- `git diff --check`: passed.

## Round 1 remediation

The build job now verifies the release tag before it builds the package.

- `scripts/verify_release_tag.py` reads `[project].version` with `tomllib`.
- It compares `GITHUB_REF_NAME` with the exact `v${version}` value.
- It stops with an explicit error when the values differ.
- Unit tests cover `v0.2.0` and reject `v0.2.1`.
- Workflow-content tests confirm the gate runs before `python -m build`.

Round 1 verification:

- `python -m pytest tests/ -q`: 283 passed.
- `python -m pytest tests/test_release_tag.py -q`: 2 passed.
- Publish workflow YAML syntax: passed with PyYAML.
- `ruff check --fix .`: passed.
- `ruff format .`: passed.
- `python -m build`: built the `0.2.0` wheel and source archive.
- `python -m twine check dist/*`: both artifacts passed.
- `git diff --check`: passed.

## Files

- `.github/workflows/publish.yml`
- `tests/test_publish_workflow.py`
- `task-2-report.md`
