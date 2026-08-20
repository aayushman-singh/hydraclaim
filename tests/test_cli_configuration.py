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

    with pytest.raises(config.ConfigurationError) as exc:
        ask.main(["Who owns launch?"])

    assert "HYDRADB_URL" in str(exc.value)
    assert "HYDRADB_TOKEN" in str(exc.value)
    assert capsys.readouterr().err == ""


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

    with pytest.raises(config.ConfigurationError) as exc:
        command.main(arguments)

    assert "HYDRADB_URL" in str(exc.value)
    assert capsys.readouterr().err == ""


def test_extract_reports_missing_llm_settings_before_file_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(config.ConfigurationError) as exc:
        extract.main(["does-not-exist.json"])

    assert "LLM_API_KEY" in str(exc.value)
    assert capsys.readouterr().err == ""


def test_pipeline_reports_missing_llm_settings_before_connect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    def fail_if_called():
        raise AssertionError("HydraDB connection must not start")

    monkeypatch.setattr(config, "connect", fail_if_called)

    with pytest.raises(config.ConfigurationError) as exc:
        pipeline.main(["does-not-exist.json"])

    assert "LLM_API_KEY" in str(exc.value)
    assert capsys.readouterr().err == ""
