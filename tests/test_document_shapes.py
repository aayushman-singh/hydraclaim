from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from hydraclaim import cli, config
from hydraclaim.errors import ValidationError


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({}, "scenario_id"),
        (
            {"scenario_id": "scenario", "entities": [], "sessions": {}},
            "sessions must be a list",
        ),
    ],
)
def test_extract_rejects_invalid_document_shape_before_extraction(
    tmp_path, monkeypatch, document, message
):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "require_settings", lambda **kwargs: None)

    from hydraclaim import extract

    with pytest.raises(ValidationError, match=message):
        extract.main([str(path)])


@pytest.mark.parametrize(
    ("scenario", "drafts", "message"),
    [
        ({}, {"drafts": []}, "scenario_id"),
        (
            {"scenario_id": "scenario", "entities": [], "ground_truth": {}},
            {"drafts": []},
            "ground_truth.claims",
        ),
        (
            {
                "scenario_id": "scenario",
                "entities": [],
                "ground_truth": {"claims": []},
            },
            {"drafts": {}},
            "drafts must be a list",
        ),
    ],
)
def test_evaluate_rejects_invalid_document_shapes(tmp_path, scenario, drafts, message):
    scenario_path = tmp_path / "scenario.json"
    drafts_path = tmp_path / "drafts.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    drafts_path.write_text(json.dumps(drafts), encoding="utf-8")

    from hydraclaim import evaluate

    with pytest.raises(ValidationError, match=message):
        evaluate.main([str(scenario_path), str(drafts_path)])


@pytest.mark.parametrize(
    ("command", "arguments", "message"),
    [
        ("extract", lambda path: [str(path)], "scenario_id"),
        ("evaluate", lambda paths: [str(paths[0]), str(paths[1])], "scenario_id"),
    ],
)
def test_unified_commands_report_invalid_document_without_traceback(
    tmp_path, monkeypatch, capsys, command, arguments, message
):
    scenario_path = tmp_path / "scenario.json"
    drafts_path = tmp_path / "drafts.json"
    scenario_path.write_text(json.dumps({}), encoding="utf-8")
    drafts_path.write_text(json.dumps({"drafts": []}), encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    paths = scenario_path if command == "extract" else (scenario_path, drafts_path)
    assert cli.main([command, *arguments(paths)]) == 1

    stderr = capsys.readouterr().err
    assert "hydraclaim: error: validation_error:" in stderr
    assert message in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize("module", ["hydraclaim.extract", "hydraclaim.evaluate"])
def test_documented_module_forms_report_invalid_root_without_traceback(
    tmp_path, module
):
    scenario_path = tmp_path / "scenario.json"
    drafts_path = tmp_path / "drafts.json"
    scenario_path.write_text(json.dumps({}), encoding="utf-8")
    drafts_path.write_text(json.dumps({"drafts": []}), encoding="utf-8")
    arguments = [str(scenario_path)]
    if module.endswith("evaluate"):
        arguments.append(str(drafts_path))
    environment = os.environ.copy()
    environment["LLM_API_KEY"] = "test-key"

    completed = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 1
    assert "hydraclaim: error: validation_error:" in completed.stderr
    assert "scenario_id" in completed.stderr
    assert "Traceback" not in completed.stderr
