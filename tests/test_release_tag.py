from pathlib import Path

import pytest

from scripts.verify_release_tag import verify_release_tag


ROOT = Path(__file__).resolve().parents[1]
PROJECT_FILE = ROOT / "pyproject.toml"


def test_release_tag_accepts_project_version() -> None:
    verify_release_tag("v0.2.0", PROJECT_FILE)


def test_release_tag_rejects_different_project_version() -> None:
    with pytest.raises(SystemExit, match="does not match project version"):
        verify_release_tag("v0.2.1", PROJECT_FILE)
