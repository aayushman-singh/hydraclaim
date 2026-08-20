from pathlib import Path
import tarfile
import tomllib
import zipfile

import hydraclaim
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_project_metadata_is_complete() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "hydraclaim"
    assert project["version"] == "0.2.0"
    assert project["scripts"]["hydraclaim"] == "hydraclaim.cli:main"
    assert project["requires-python"] == ">=3.11"


def test_readme_uses_installed_command() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "pip install hydraclaim" in text
    assert "hydraclaim ask" in text
    assert "hydraclaim serve" in text


def test_package_verifier_selects_only_the_release_wheel() -> None:
    text = (ROOT / "scripts" / "verify-package.ps1").read_text(encoding="utf-8")

    assert '$ErrorActionPreference = "Stop"' in text
    assert '$expectedWheelName = "hydraclaim-0.2.0-py3-none-any.whl"' in text
    assert "Where-Object Name -eq $expectedWheelName" in text
    assert "$matchingWheels.Count -ne 1" in text
    assert "$wheelPath" in text
    assert '& $python -m pip install --disable-pip-version-check "httpx>=0.27"' in text
    assert (
        "& $python -m pip install --disable-pip-version-check "
        "--no-index --no-deps $wheelPath"
    ) in text


def test_project_declares_hatchling_and_explicit_package_inclusion() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["build-system"] == {
        "requires": ["hatchling>=1.27"],
        "build-backend": "hatchling.build",
    }
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "hydraclaim"
    ]


def test_source_archive_excludes_tests_and_build_artifacts() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    exclusions = metadata["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    required_exclusions = {
        "/tests",
        "/hydradb-data/**",
        "/.superpowers/**",
        "/.claude/**",
        "/.playwright-mcp/**",
        "/docs/agents/**",
        "/docs/superpowers/**",
        "/AGENTS.md",
        "/CONTEXT.md",
        "/PLAN.md",
        "/task-*-brief.md",
        "/task-*-report.md",
        "/data/sessions/**",
        "/data/longmemeval/**",
        "/results/**",
        "/*.egg-info/**",
        "/.env",
        "/.env.*",
        "/.pytest_cache/**",
        "/.ruff_cache/**",
        "/.worktrees/**",
        "/build/**",
        "/dist/**",
        "/.venv/**",
        "/.venv-package-test/**",
    }
    assert required_exclusions <= set(exclusions)


def test_package_does_not_duplicate_project_version() -> None:
    assert not hasattr(hydraclaim, "__version__")


def test_license_expression_has_no_obsolete_license_classifier() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        "License :: OSI Approved :: MIT License"
        not in metadata["project"]["classifiers"]
    )


def test_distribution_selects_only_hydraclaim_packages() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "hydraclaim"
    ]


def test_distribution_includes_schema_resource() -> None:
    assert (ROOT / "hydraclaim" / "schema.cypher").is_file()


def test_release_archives_exclude_local_and_generated_content() -> None:
    wheel_path = next((ROOT / "dist").glob("*.whl"), None)
    sdist_path = next((ROOT / "dist").glob("*.tar.gz"), None)
    if wheel_path is None or sdist_path is None:
        pytest.skip("build artifacts are not present")

    with zipfile.ZipFile(wheel_path) as archive:
        wheel_names = archive.namelist()
        metadata_name = next(
            name for name in wheel_names if name.endswith(".dist-info/METADATA")
        )
        wheel_metadata = archive.read(metadata_name).decode("utf-8")
    with tarfile.open(sdist_path) as archive:
        sdist_names = archive.getnames()

    forbidden = {
        ".env",
        ".env.local",
        ".superpowers",
        ".claude",
        ".playwright-mcp",
        ".worktrees",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".hypothesis",
        ".cache",
        ".tox",
        ".nox",
        "__pycache__",
        "tests",
        "build",
        "dist",
        ".venv",
        ".venv-package-test",
        "venv",
        "env",
        "hydradb-data",
        "sessions",
        "longmemeval",
        "results",
        "auth-token",
    }

    def forbidden_name(name: str) -> bool:
        relative_name = name.split("/", 1)[-1]
        parts = relative_name.split("/")
        return (
            any(
                part in forbidden
                or part.endswith(".egg-info")
                or part.startswith(".env")
                or part == "auth-token"
                or part.endswith(".pyc")
                for part in parts
            )
            or (
                len(parts) >= 2
                and parts[0] == "demo"
                and (
                    parts[-1].endswith((".mp4", ".png"))
                    or parts[-1].endswith("-output.txt")
                    or (parts[-1].startswith("thumb") and parts[-1].endswith(".jpg"))
                )
            )
            or parts[-1] in {"AGENTS.md", "CONTEXT.md", "PLAN.md"}
            or (
                parts[-1].startswith("task-")
                and parts[-1].endswith(("-brief.md", "-report.md"))
            )
        )

    for archive_type, names in (("wheel", wheel_names), ("sdist", sdist_names)):
        assert any(name.endswith("hydraclaim/cli.py") for name in names)
        assert any(name.endswith("hydraclaim/schema.cypher") for name in names)
        assert not [name for name in names if forbidden_name(name)], archive_type

    sdist_root = sdist_path.name.removesuffix(".tar.gz") + "/"
    assert f"{sdist_root}README.md" in sdist_names
    assert f"{sdist_root}LICENSE" in sdist_names

    wheel_dist_info = metadata_name.removesuffix("METADATA")
    assert f"{wheel_dist_info}licenses/LICENSE" in wheel_names
    assert "License-File: LICENSE" in wheel_metadata
    readme = (ROOT / "README.md").read_text(encoding="utf-8").strip()
    assert readme in wheel_metadata
