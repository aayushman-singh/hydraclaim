from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def _workflow(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _trigger(data: dict) -> dict:
    # PyYAML 1.1 loads the YAML 1.2 key `on` as the boolean True.
    trigger = data.get("on", data.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _steps(data: dict, job_name: str) -> list[dict]:
    steps = data["jobs"][job_name]["steps"]
    assert isinstance(steps, list)
    return steps


def _run_commands(data: dict, job_name: str = "build") -> list[str]:
    return [step["run"] for step in _steps(data, job_name) if "run" in step]


def _assert_staged_tests(data: dict, job_name: str) -> None:
    commands = _run_commands(data, job_name)
    required = [
        'python -m pytest tests/ -v -m "not artifact"',
        "python -c \"import shutil; shutil.rmtree('dist', ignore_errors=True)\"",
        "python -m build",
        "python -m twine check dist/*",
        "python -m pytest tests/ -v -m artifact",
    ]
    for command in required:
        assert commands.count(command) == 1
    positions = [commands.index(command) for command in required]
    assert positions == sorted(positions)


def test_workflows_parse_as_yaml() -> None:
    _workflow(WORKFLOW)
    _workflow(CI_WORKFLOW)


def test_publish_workflow_is_tag_only_yaml() -> None:
    data = _workflow(WORKFLOW)
    assert _trigger(data) == {"push": {"tags": ["v*"]}}


def test_publish_workflow_uses_least_privilege_and_trusted_publishing() -> None:
    data = _workflow(WORKFLOW)
    assert data["permissions"] == {"contents": "read"}
    publish = data["jobs"]["publish"]
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/hydraclaim",
    }
    build_steps = _steps(data, "build")
    assert build_steps[0]["with"]["persist-credentials"] is False
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "PYPI_API_TOKEN" not in text
    assert "secrets." not in text


def test_all_external_actions_use_exact_immutable_pins() -> None:
    for workflow_path in (WORKFLOW, CI_WORKFLOW):
        data = _workflow(workflow_path)
        for job in data["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if not uses:
                    continue
                action, sha_comment = uses.split("@", 1)
                sha = sha_comment.split(" ", 1)[0]
                assert action in PINS
                assert sha == PINS[action]


def test_publish_workflow_tests_and_uploads_exact_build_artifacts() -> None:
    data = _workflow(WORKFLOW)
    _assert_staged_tests(data, "build")
    build_steps = _steps(data, "build")
    upload = next(
        step for step in build_steps if "upload-artifact" in step.get("uses", "")
    )
    assert upload["with"] == {
        "name": "python-package-distributions",
        "path": "dist/",
        "if-no-files-found": "error",
    }


def test_publish_workflow_runs_source_tests_before_build_and_artifact_tests_after() -> (
    None
):
    _assert_staged_tests(_workflow(WORKFLOW), "build")


def test_ci_workflow_runs_source_tests_before_build_and_artifact_tests_after() -> None:
    _assert_staged_tests(_workflow(CI_WORKFLOW), "test")


def test_publish_workflow_checks_tag_against_project_version_before_build() -> None:
    data = _workflow(WORKFLOW)
    commands = _run_commands(data)
    assert "python scripts/verify_release_tag.py" in commands
    tag_step = next(
        step
        for step in _steps(data, "build")
        if "verify_release_tag" in step.get("run", "")
    )
    assert tag_step["env"] == {"GITHUB_REF_NAME": "${{ github.ref_name }}"}
    assert commands.index("python scripts/verify_release_tag.py") < commands.index(
        "python -m build"
    )


def test_release_tag_gate_uses_stdlib_tomllib_and_explicit_mismatch_message() -> None:
    script = (ROOT / "scripts" / "verify_release_tag.py").read_text(encoding="utf-8")
    assert "import tomllib" in script
    assert "tomllib.loads" in script
    assert "GITHUB_REF_NAME" in script
    assert "does not match project version" in script


def test_publish_job_only_downloads_and_publishes() -> None:
    data = _workflow(WORKFLOW)
    steps = _steps(data, "publish")
    assert len(steps) == 2
    assert steps[0]["uses"].startswith("actions/download-artifact@")
    assert steps[0]["with"] == {
        "name": "python-package-distributions",
        "path": "dist/",
    }
    assert steps[1]["uses"].startswith("pypa/gh-action-pypi-publish@")


def test_ci_linux_clean_wheel_acceptance_is_explicit() -> None:
    data = _workflow(CI_WORKFLOW)
    step = next(
        step
        for step in _steps(data, "test")
        if step.get("name") == "Linux clean-wheel acceptance"
    )
    assert step["if"] == "runner.os == 'Linux'"
    assert "pwsh" in step["run"]
    assert "exit 1" in step["run"]
