from __future__ import annotations

import pytest

from hydraclaim import ask, benchmark, config, extract, ingest, pipeline, schema, serve


def test_ask_reports_missing_hydradb_settings_before_connect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HYDRADB_URL", "")
    monkeypatch.setenv("HYDRADB_TOKEN", "")

    def fail_if_called():
        raise AssertionError("HydraDB connection must not start")

    monkeypatch.setattr(config, "connect", fail_if_called)

    with pytest.raises(SystemExit) as exc:
        ask.main(["Who owns launch?"])

    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "HYDRADB_URL" in error
    assert "HYDRADB_TOKEN" in error


@pytest.mark.parametrize(
    ("command", "arguments"),
    (
        (ingest, ["does-not-exist.json"]),
        (schema, ["--verify"]),
        (benchmark, ["does-not-exist.json"]),
        (serve, ["--port", "0"]),
    ),
)
def test_hydradb_commands_fail_before_network_access(
    command,
    arguments,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HYDRADB_URL", "")
    monkeypatch.setenv("HYDRADB_TOKEN", "")

    def fail_if_called():
        raise AssertionError("HydraDB connection must not start")

    monkeypatch.setattr(config, "connect", fail_if_called)

    with pytest.raises(SystemExit) as exc:
        command.main(arguments)

    assert exc.value.code == 2
    assert "HYDRADB_URL" in capsys.readouterr().err


def test_extract_reports_missing_llm_settings_before_file_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(SystemExit) as exc:
        extract.main(["does-not-exist.json"])

    assert exc.value.code == 2
    assert "LLM_API_KEY" in capsys.readouterr().err


def test_pipeline_reports_missing_llm_settings_before_connect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    def fail_if_called():
        raise AssertionError("HydraDB connection must not start")

    monkeypatch.setattr(config, "connect", fail_if_called)

    with pytest.raises(SystemExit) as exc:
        pipeline.main(["does-not-exist.json"])

    assert exc.value.code == 2
    assert "LLM_API_KEY" in capsys.readouterr().err
