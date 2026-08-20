from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_publish_workflow_is_tag_only_yaml() -> None:
    lines = _workflow_text().splitlines()

    assert lines[0] == "name: Publish"
    assert "on:" in lines
    assert "  push:" in lines
    assert '    tags: ["v*"]' in lines
    assert not any(
        line.strip() in {"pull_request:", "workflow_dispatch:"} for line in lines
    )


def test_publish_workflow_uses_least_privilege_and_trusted_publishing() -> None:
    text = _workflow_text()

    assert "permissions:\n  contents: read" in text
    assert "    permissions:\n      id-token: write" in text
    assert "persist-credentials: false" in text
    assert "      name: pypi" in text
    assert "      url: https://pypi.org/p/hydraclaim" in text
    assert "pypa/gh-action-pypi-publish@release/v1" in text
    assert "PYPI_API_TOKEN" not in text
    assert "secrets." not in text
    assert not any(line.strip().startswith("token:") for line in text.splitlines())


def test_publish_workflow_tests_and_uploads_exact_build_artifacts() -> None:
    text = _workflow_text()

    required_actions = (
        "actions/checkout@v7",
        "actions/setup-python@v7",
        "actions/upload-artifact@v7",
        "actions/download-artifact@v8",
    )
    for action in required_actions:
        assert f"uses: {action}" in text

    required_commands = (
        "python -m build",
        "python -m twine check dist/*",
        "python -m pytest tests/ -v",
        "python -m pytest tests/test_package_metadata.py -v",
    )
    positions = [text.index(command) for command in required_commands]
    assert positions == sorted(positions)
    assert text.count("python -m build") == 1
    assert "          name: python-package-distributions" in text
    assert "          path: dist/" in text
    assert text.count("          path: dist/") == 2


def test_publish_job_only_downloads_and_publishes() -> None:
    text = _workflow_text()
    publish_section = text.split("  publish:\n", maxsplit=1)[1]
    steps_section = publish_section.split("    steps:\n", maxsplit=1)[1]

    assert steps_section.startswith(
        "      - uses: actions/download-artifact@v8\n"
        "        with:\n"
        "          name: python-package-distributions\n"
        "          path: dist/\n"
        "      - uses: pypa/gh-action-pypi-publish@release/v1\n"
    )
    assert "      - run:" not in steps_section
    assert "actions/checkout@" not in steps_section
    assert "actions/setup-python@" not in steps_section
