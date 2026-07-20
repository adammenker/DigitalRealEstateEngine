from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from rank_rent.cli import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setattr(
        "rank_rent.cli.get_settings",
        lambda: SimpleNamespace(project_root=PROJECT_ROOT),
    )
    return CliRunner()


def test_calibrate_validate_config_command(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _runner(monkeypatch).invoke(
        app,
        ["calibrate", "validate-config"],
    )

    assert result.exit_code == 0, result.output
    assert "Calibration configuration: VALID" in result.output
    assert "Scenarios: 26" in result.output


def test_calibrate_run_and_report_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli = _runner(monkeypatch)
    run_result = cli.invoke(
        app,
        ["calibrate", "run", "--output-dir", str(tmp_path)],
    )

    assert run_result.exit_code == 0, run_result.output
    assert "Result: PASS" in run_result.output
    assert "Network attempts: 0" in run_result.output
    report_path = next(tmp_path.glob("*-calibration-*.json"))

    report_result = cli.invoke(
        app,
        ["calibrate", "report", str(report_path), "--format", "json"],
    )
    assert report_result.exit_code == 0, report_result.output
    assert '"success": true' in report_result.output


def test_calibrate_compare_command_is_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _runner(monkeypatch).invoke(
        app,
        ["calibrate", "compare", "v2.12", "v2.12"],
    )

    assert result.exit_code == 0, result.output
    assert "Scoring comparison v2.12 -> v2.12" in result.output
    assert "Network attempts: 0" in result.output
