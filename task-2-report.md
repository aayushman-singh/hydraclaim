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

## Files

- `.github/workflows/publish.yml`
- `tests/test_publish_workflow.py`
- `task-2-report.md`
