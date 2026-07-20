from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rank_rent.calibration.models import CalibrationReport, ComparisonReport


def save_report(
    report: CalibrationReport | ComparisonReport,
    output_directory: Path,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"{report.created_at:%Y%m%dT%H%M%SZ}-{_report_name(report)}.json"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(report.model_dump_json(indent=2))
            handle.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"Refusing to overwrite historical calibration report {path}") from exc
    return path


def load_report(path: Path) -> CalibrationReport:
    return CalibrationReport.model_validate_json(path.read_text(encoding="utf-8"))


def latest_report(reports_directory: Path) -> Path:
    reports = sorted(reports_directory.glob("*-calibration-*.json"))
    if not reports:
        raise FileNotFoundError(f"No calibration reports found in {reports_directory}")
    return reports[-1]


def render_report(report: CalibrationReport, *, output_format: str = "text") -> str:
    if output_format == "json":
        return report.model_dump_json(indent=2)
    failed_scenarios = [
        result.scenario_id for result in report.scenario_results if not result.passed
    ]
    failed_pairs = [
        f"{result.preferred}>{result.over}"
        for result in report.pairwise_results
        if not result.passed
    ]
    lines = [
        f"Calibration report {report.report_id}",
        f"Suite: {report.suite_version}",
        f"Scoring: {report.scoring_version} ({report.scoring_config_hash})",
        f"Configuration hash: {report.benchmark_config_hash}",
        (
            "Scenarios: "
            f"{report.scenario_summary.passed}/{report.scenario_summary.total} passed"
        ),
        (
            "Pairwise expectations: "
            f"{report.pairwise_summary.passed}/{report.pairwise_summary.total} passed"
        ),
        (
            "SERP classification: "
            f"{report.classification_summary['passed']}/"
            f"{report.classification_summary['total']} passed "
            f"({report.classification_summary['accuracy']:.1%})"
        ),
        (
            "Provider benchmark: "
            f"{report.provider_summary['passed']}/"
            f"{report.provider_summary['total']} passed "
            f"({report.provider_summary['accuracy']:.1%})"
        ),
        f"Network attempts: {report.network_attempt_count}",
        f"Result: {'PASS' if report.success else 'FAIL'}",
    ]
    if failed_scenarios:
        lines.append(f"Failed scenarios: {', '.join(failed_scenarios)}")
    if failed_pairs:
        lines.append(f"Failed pairwise expectations: {', '.join(failed_pairs)}")
    return "\n".join(lines)


def render_comparison(report: ComparisonReport, *, output_format: str = "text") -> str:
    if output_format == "json":
        return report.model_dump_json(indent=2)
    changed = [item for item in report.scenario_diffs if item.score_delta != 0]
    lines = [
        f"Scoring comparison {report.version_a} -> {report.version_b}",
        f"Configuration A: {report.config_hash_a}",
        f"Configuration B: {report.config_hash_b}",
        f"Changed scenarios: {len(changed)}/{len(report.scenario_diffs)}",
        f"Pairwise regressions: {len(report.pairwise_regressions)}",
        f"Network attempts: {report.network_attempt_count}",
    ]
    lines.extend(
        f"{item.scenario_id}: {item.score_a:.2f} -> {item.score_b:.2f} "
        f"({item.score_delta:+.2f})"
        for item in sorted(changed, key=lambda value: abs(value.score_delta), reverse=True)
    )
    return "\n".join(lines)


def report_payload(report: CalibrationReport | ComparisonReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _report_name(report: CalibrationReport | ComparisonReport) -> str:
    if isinstance(report, CalibrationReport):
        return f"calibration-{report.scoring_version}-{report.report_id}"
    return f"comparison-{report.version_a}-to-{report.version_b}"
