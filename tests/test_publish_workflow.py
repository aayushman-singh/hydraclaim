from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_text(workflow: Path = WORKFLOW) -> str:
    return workflow.read_text(encoding="utf-8")


def _assert_staged_tests(text: str) -> None:
    source_tests = 'python -m pytest tests/ -v -m "not artifact"'
    clean_dist = (
        "python -c \"import shutil; shutil.rmtree('dist', ignore_errors=True)\""
    )
    build = "python -m build"
    twine_check = "python -m twine check dist/*"
    artifact_tests = "python -m pytest tests/ -v -m artifact"
    commands = [
        line.strip()[len("- run: ") :]
        for line in text.splitlines()
        if line.strip().startswith("- run: ")
    ]

    assert commands.count(source_tests) == 1
    assert commands.count(clean_dist) == 1
    assert commands.count(build) == 1
    assert commands.count(twine_check) == 1
    assert commands.count(artifact_tests) == 1
    positions = [
        commands.index(command)
        for command in (source_tests, clean_dist, build, twine_check, artifact_tests)
    ]
    assert positions == sorted(positions)


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
        'python -m pytest tests/ -v -m "not artifact"',
        "python -c \"import shutil; shutil.rmtree('dist', ignore_errors=True)\"",
        "python -m build",
        "python -m twine check dist/*",
        "python -m pytest tests/ -v -m artifact",
    )
    positions = [text.index(command) for command in required_commands]
    assert positions == sorted(positions)
    assert text.count("python -m build") == 1
    assert "          name: python-package-distributions" in text
    assert "          path: dist/" in text
    assert text.count("          path: dist/") == 2


def test_publish_workflow_runs_source_tests_before_build_and_artifact_tests_after() -> (
    None
):
    _assert_staged_tests(_workflow_text())


def test_ci_workflow_runs_source_tests_before_build_and_artifact_tests_after() -> None:
    _assert_staged_tests(_workflow_text(CI_WORKFLOW))


def test_publish_workflow_checks_tag_against_project_version_before_build() -> None:
    text = _workflow_text()

    gate = "python scripts/verify_release_tag.py"
    assert gate in text
    assert "        env:\n          GITHUB_REF_NAME: ${{ github.ref_name }}" in text
    assert text.index(gate) < text.index("python -m build")


def test_release_tag_gate_uses_stdlib_tomllib_and_explicit_mismatch_message() -> None:
    script = (ROOT / "scripts" / "verify_release_tag.py").read_text(encoding="utf-8")

    assert "import tomllib" in script
    assert "tomllib.loads" in script
    assert "GITHUB_REF_NAME" in script
    assert "does not match project version" in script


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
