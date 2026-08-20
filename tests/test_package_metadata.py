from pathlib import Path
import tomllib

import hydraclaim


ROOT = Path(__file__).resolve().parents[1]


def test_project_metadata_is_complete() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "hydraclaim"
    assert project["version"] == "0.2.0"
    assert project["scripts"]["hydraclaim"] == "hydraclaim.cli:main"
    assert project["requires-python"] == ">=3.11"


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

    assert metadata["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"] == [
        "/tests",
        "/build",
        "/dist",
        "/.venv-package-test",
    ]


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
