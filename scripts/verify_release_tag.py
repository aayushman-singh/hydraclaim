"""Verify that the GitHub release tag matches the project version."""

from __future__ import annotations

import os
from pathlib import Path
import tomllib


PROJECT_FILE = Path(__file__).resolve().parents[1] / "pyproject.toml"


def project_tag(project_file: Path = PROJECT_FILE) -> str:
    metadata = tomllib.loads(project_file.read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    return f"v{version}"


def verify_release_tag(tag: str, project_file: Path = PROJECT_FILE) -> None:
    expected_tag = project_tag(project_file)
    if tag != expected_tag:
        raise SystemExit(
            f"Release tag {tag!r} does not match project version; "
            f"expected {expected_tag!r}."
        )


def main() -> None:
    tag = os.environ.get("GITHUB_REF_NAME")
    if not tag:
        raise SystemExit("Release tag check failed: GITHUB_REF_NAME is not set.")
    verify_release_tag(tag)
    print(f"Release tag {tag!r} matches the project version.")


if __name__ == "__main__":
    main()
