from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_license_expression_has_no_obsolete_license_classifier() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        "License :: OSI Approved :: MIT License"
        not in metadata["project"]["classifiers"]
    )


def test_distribution_selects_only_hydraclaim_packages() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "hydraclaim*"
    ]


def test_distribution_includes_schema_resource() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        "schema.cypher" in metadata["tool"]["setuptools"]["package-data"]["hydraclaim"]
    )
