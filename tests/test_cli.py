"""Tests for the installed HydraClaim command dispatcher."""

from __future__ import annotations

import pytest

from hydraclaim.cli import COMMANDS, main


COMMAND_NAMES = (
    "ask",
    "serve",
    "schema",
    "generate",
    "ingest",
    "extract",
    "evaluate",
    "pipeline",
    "benchmark",
    "longmemeval",
)


def test_dispatcher_has_exactly_the_public_commands() -> None:
    assert tuple(COMMANDS) == COMMAND_NAMES
    assert len(COMMANDS) == 10


def test_root_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "hydraclaim 0.2.0"


def test_root_help_without_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    for command in COMMAND_NAMES:
        assert command in output


@pytest.mark.parametrize("command", COMMAND_NAMES)
def test_subcommand_help(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0


def test_unknown_command_returns_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["unknown"]) == 2
    assert "unknown command" in capsys.readouterr().err
