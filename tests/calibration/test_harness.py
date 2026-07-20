from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rank_rent.calibration.network import CalibrationNetworkAccessError, NetworkGuard
from rank_rent.calibration.reporting import latest_report, load_report, save_report
from rank_rent.calibration.runner import CalibrationRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def runner() -> CalibrationRunner:
    return CalibrationRunner(PROJECT_ROOT)


@pytest.fixture(scope="module")
def report(runner: CalibrationRunner) -> Any:
    return runner.run()


def test_versioned_scenario_library_is_complete_and_green(
    runner: CalibrationRunner,
    report: Any,
) -> None:
    assert runner.scenario_library.schema_version == 1
    assert len(runner.scenario_library.scenarios) >= 24
    assert report.scenario_summary.total == len(runner.scenario_library.scenarios)
    assert report.scenario_summary.failed == 0
    assert report.success is True


def test_business_direction_pairwise_expectations_pass(report: Any) -> None:
    assert report.pairwise_summary.total >= 10
    assert report.pairwise_summary.failed == 0
    assert all(item.preferred_value > item.other_value for item in report.pairwise_results)


def test_serp_classification_benchmark_covers_every_supported_class(report: Any) -> None:
    expected_classes = {
        "local_provider",
        "directory",
        "marketplace",
        "lead_generator",
        "national_brand",
        "informational_publisher",
        "government_or_nonprofit",
        "unknown",
    }
    assert expected_classes <= set(report.classification_summary["confusion"])
    assert report.classification_summary["accuracy"] == 1


def test_provider_benchmark_covers_distinct_suitability_signals(report: Any) -> None:
    case_ids = {
        case["case_id"] for case in report.provider_summary["cases"]
    }
    assert {
        "adjacent_service_only",
        "confirmed_active",
        "unknown_status",
        "closed_business",
        "in_market",
        "outside_market",
        "directly_contactable",
        "non_contactable",
        "strong_review_evidence",
        "sparse_review_evidence",
    } <= case_ids
    assert report.provider_summary["accuracy"] == 1


def test_calibration_run_and_comparison_attempt_zero_network(
    runner: CalibrationRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def record_attempt(
        self: NetworkGuard,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        attempted.append((args, kwargs))
        raise CalibrationNetworkAccessError("network attempted")

    monkeypatch.setattr(NetworkGuard, "deny", record_attempt)

    report = runner.run()
    comparison = runner.compare("v2.12", "v2.12")

    assert attempted == []
    assert report.network_attempt_count == 0
    assert comparison.network_attempt_count == 0
    assert comparison.pairwise_regressions == []
    assert all(item.score_delta == 0 for item in comparison.scenario_diffs)


def test_comparison_reports_real_version_diffs_without_network(
    tmp_path: Path,
) -> None:
    source_config = PROJECT_ROOT / "config/benchmarks/scoring/v2.12.yaml"
    next_config = tmp_path / "v2.13.yaml"
    next_config.write_text(
        source_config.read_text(encoding="utf-8")
        .replace('version: "v2.12"', 'version: "v2.13"', 1)
        .replace(
            "service_attractiveness_share: 0.65\n  market_attractiveness_share: 0.35",
            "service_attractiveness_share: 0.60\n  market_attractiveness_share: 0.40",
            1,
        ),
        encoding="utf-8",
    )
    source_manifest = PROJECT_ROOT / "config/benchmarks/manifest.yaml"
    temporary_manifest = tmp_path / "manifest.yaml"
    temporary_manifest.write_text(
        source_manifest.read_text(encoding="utf-8").replace(
            "  v2.12: config/benchmarks/scoring/v2.12.yaml",
            (
                "  v2.12: config/benchmarks/scoring/v2.12.yaml\n"
                f"  v2.13: {next_config}"
            ),
            1,
        ),
        encoding="utf-8",
    )
    comparison = CalibrationRunner(
        PROJECT_ROOT,
        manifest_path=temporary_manifest,
    ).compare("v2.12", "v2.13")

    assert comparison.network_attempt_count == 0
    assert comparison.config_hash_a != comparison.config_hash_b
    assert any(item.score_delta != 0 for item in comparison.scenario_diffs)


def test_config_validation_covers_versioning_hashes_and_weight_invariants(
    runner: CalibrationRunner,
) -> None:
    validation = runner.validate_config()
    check_names = {check.check for check in validation.checks}

    assert validation.valid is True
    assert len(validation.benchmark_config_hash) == 64
    assert "v2.12:declared_version" in check_names
    assert "v2.12:weights_total" in check_names
    assert "v2.12:demand_shares" in check_names
    assert "v2.12:commercial_signal_shares" in check_names


def test_config_validation_rejects_version_mismatch(
    runner: CalibrationRunner,
    tmp_path: Path,
) -> None:
    bad_config = tmp_path / "v2.12.yaml"
    source = PROJECT_ROOT / "config/benchmarks/scoring/v2.12.yaml"
    bad_config.write_text(
        source.read_text(encoding="utf-8").replace(
            'version: "v2.12"',
            'version: "v-next"',
            1,
        ),
        encoding="utf-8",
    )
    original = runner.manifest.scoring_configs["v2.12"]
    runner.manifest.scoring_configs["v2.12"] = str(bad_config)
    try:
        validation = runner.validate_config()
    finally:
        runner.manifest.scoring_configs["v2.12"] = original

    assert validation.valid is False
    version_check = next(
        check
        for check in validation.checks
        if check.check == "v2.12:declared_version"
    )
    assert version_check.passed is False


def test_historical_reports_are_immutable_and_loadable(
    runner: CalibrationRunner,
    tmp_path: Path,
) -> None:
    report = runner.run()
    path = save_report(report, tmp_path)

    assert latest_report(tmp_path) == path
    assert load_report(path) == report
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        save_report(report, tmp_path)
