"""Tests for the installed HydraClaim command dispatcher."""

from __future__ import annotations

import pytest

from hydraclaim import cli
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
    "record",
    "process",
    "status",
    "events",
)


COMMAND_SETTINGS = {
    "ask": (
        "HYDRADB_URL",
        "HYDRADB_TOKEN",
        "HYDRADB_NAMESPACE",
        "HYDRADB_GRAPH",
        "HYDRADB_CELL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "--llm",
    ),
    "serve": (
        "HYDRADB_URL",
        "HYDRADB_TOKEN",
        "HYDRADB_NAMESPACE",
        "HYDRADB_GRAPH",
        "HYDRADB_CELL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "--llm",
    ),
    "schema": (
        "HYDRADB_URL",
        "HYDRADB_TOKEN",
        "HYDRADB_NAMESPACE",
        "HYDRADB_GRAPH",
        "HYDRADB_CELL",
    ),
    "ingest": (
        "HYDRADB_URL",
        "HYDRADB_TOKEN",
        "HYDRADB_NAMESPACE",
        "HYDRADB_GRAPH",
        "HYDRADB_CELL",
    ),
    "extract": ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"),
    "pipeline": (
        "HYDRADB_URL",
        "HYDRADB_TOKEN",
        "HYDRADB_NAMESPACE",
        "HYDRADB_GRAPH",
        "HYDRADB_CELL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
    ),
    "benchmark": (
        "HYDRADB_URL",
        "HYDRADB_TOKEN",
        "HYDRADB_NAMESPACE",
        "HYDRADB_GRAPH",
        "HYDRADB_CELL",
    ),
}


def test_dispatcher_has_exactly_the_public_commands() -> None:
    assert tuple(COMMANDS) == COMMAND_NAMES
    assert len(COMMANDS) == len(COMMAND_NAMES)


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


@pytest.mark.parametrize("command,settings", COMMAND_SETTINGS.items())
def test_external_command_help_names_required_settings(
    command: str, settings: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    for setting in settings:
        assert setting in output


def test_external_command_help_explains_explicit_llm_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ask", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "LLM_API_KEY alone does not select LLM mode" in output


def test_unknown_command_returns_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["unknown"]) == 2
    assert "unknown command" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (cli.ConfigurationError("HYDRADB_URL is missing"), "configuration_error"),
        (ValueError("invalid claim"), "validation_error"),
        (FileNotFoundError("missing.json"), "file_error"),
        (cli.HydraDBError("graph backend failed"), "graph_backend_failed"),
        (cli.LLMError("language model failed"), "llm_failed"),
        (cli.GraphIntegrityError("supersession cycle"), "graph_integrity_error"),
        (cli.json.JSONDecodeError("bad JSON", "{}", 0), "invalid_json"),
    ],
)
def test_installed_dispatch_maps_expected_failures(
    monkeypatch, capsys, caplog, error, code
):
    class FailingCommand:
        @staticmethod
        def main(argv):
            raise error

    monkeypatch.setattr(cli.importlib, "import_module", lambda name: FailingCommand)
    with caplog.at_level("ERROR", logger="hydraclaim.cli"):
        assert main(["ask", "question"]) == 1
    stderr = capsys.readouterr().err
    assert f"hydraclaim: error: {code}:" in stderr
    assert "Traceback" in caplog.text


def test_installed_dispatch_does_not_hide_unexpected_programming_errors(monkeypatch):
    class BrokenCommand:
        @staticmethod
        def main(argv):
            raise RuntimeError("programming error")

    monkeypatch.setattr(cli.importlib, "import_module", lambda name: BrokenCommand)
    with pytest.raises(RuntimeError, match="programming error"):
        main(["ask", "question"])


def test_installed_dispatch_maps_module_configuration_failure(monkeypatch, capsys):
    monkeypatch.setenv("HYDRADB_URL", "")
    monkeypatch.setenv("HYDRADB_TOKEN", "")

    assert main(["ask", "question"]) == 1
    stderr = capsys.readouterr().err
    assert "hydraclaim: error: configuration_error:" in stderr
    assert "HYDRADB_URL" in stderr
