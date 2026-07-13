from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class CapabilityResult:
    integration: str
    capability: str
    passed: bool
    required_fields_found: list[str]
    missing_fields: list[str]
    sample_counts: dict[str, int]
    estimated_cost: float
    actual_cost: float
    storage_restrictions_noted: str
    manual_intervention_required: bool
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fixture_capability_report(scan_result: dict[str, Any]) -> dict[str, Any]:
    checks = [
        CapabilityResult(
            integration="DataForSEO fixture",
            capability="location, keywords, metrics, SERP, competitors, providers",
            passed=True,
            required_fields_found=["location", "keywords", "metrics", "serp", "providers"],
            missing_fields=[],
            sample_counts={"providers": len(scan_result["providers"]), "domains": len(scan_result["domains"])},
            estimated_cost=0,
            actual_cost=0,
            storage_restrictions_noted="Raw paid payloads must be cached and retained as historical snapshots.",
            manual_intervention_required=False,
            blocking=True,
        ),
        CapabilityResult(
            integration="Domain availability fixture",
            capability="likely availability check",
            passed=True,
            required_fields_found=["status", "checked_at", "provider_raw_status"],
            missing_fields=[],
            sample_counts={"domains": len(scan_result["domains"])},
            estimated_cost=0,
            actual_cost=0,
            storage_restrictions_noted="Availability is not trademark clearance and does not purchase domains.",
            manual_intervention_required=True,
            blocking=False,
        ),
        CapabilityResult(
            integration="Static site generator",
            capability="provider-independent sample site build",
            passed=bool(scan_result["site_path"]),
            required_fields_found=["home", "disclosure", "privacy", "terms"],
            missing_fields=[],
            sample_counts={"sites": 1 if scan_result["site_path"] else 0},
            estimated_cost=0,
            actual_cost=0,
            storage_restrictions_noted="Generated copy requires manual review before deployment.",
            manual_intervention_required=True,
            blocking=False,
        ),
    ]
    return {
        "data_mode": scan_result.get("data_mode", "fixture"),
        "synthetic_fixture_data": scan_result.get("data_mode", "fixture") == "fixture",
        "capabilities": [check.to_dict() for check in checks],
    }
