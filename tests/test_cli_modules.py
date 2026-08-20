"""Tests for the public command module entry points."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


COMMAND_MODULES = (
    "hydraclaim.ask",
    "hydraclaim.serve",
    "hydraclaim.schema",
    "hydraclaim.generate.__main__",
    "hydraclaim.ingest",
    "hydraclaim.extract",
    "hydraclaim.evaluate",
    "hydraclaim.pipeline",
    "hydraclaim.benchmark",
    "hydraclaim.longmemeval",
)


@pytest.mark.parametrize("module_name", COMMAND_MODULES)
def test_command_help_accepts_explicit_argv(module_name: str) -> None:
    module = importlib.import_module(module_name)

    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])

    assert exc.value.code == 0


def test_dispatcher_supports_module_execution() -> None:
    source = importlib.import_module("hydraclaim.cli").__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in text
    assert "raise SystemExit(main())" in text
