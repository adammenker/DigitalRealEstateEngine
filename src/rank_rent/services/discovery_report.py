from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import ApiCallORM, ScanPlanCallORM
from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    Market,
    OpportunityScore,
    ProviderCandidate,
    SerpSnapshot,
    ServiceFamily,
)
from rank_rent.services.demand import analyze_demand
from rank_rent.services.demand_estimation import MarketDemandEstimator


def build_discovery_report(
    *,
    service: ServiceFamily,
    market: Market,
    metrics: list[KeywordMetric],
    serp_snapshots: list[SerpSnapshot],
    competitors: list[CompetitorMetric],
    providers: list[ProviderCandidate],
    score: OpportunityScore,
    scan_metadata: dict[str, Any],
    demand_estimator: MarketDemandEstimator | None = None,
    public_data_prefilter: dict[str, Any] | None = None,
    evidence_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    demand = analyze_demand(
        [metric for metric in metrics if metric.included],
        market,
        estimator=demand_estimator,
    )
    serp_results = [result for snapshot in serp_snapshots for result in snapshot.results]
    composition = _composition([result.classification for result in serp_results])
    freshness = _data_freshness(metrics, serp_snapshots, providers)
    return {
        "summary": {
            "service": service.display_name,
            "market": market.display_name,
            "score": score.total_score,
            "evidence_status": score.evidence_status,
            "confidence": score.confidence.value,
            "status": _summary_status(score),
            "explanation": score.explanation,
        },
        "market_interpretation": {
            "input_market": market.display_name,
            "market_type": market.type.value,
            "geography_id": market.geography_id,
            "geography_dataset_version": market.geography_dataset_version,
            "state": market.state,
            "county": market.county,
            "county_fips": market.county_fips,
            "metro": market.metro,
            "metro_code": market.metro_code,
            "latitude": market.latitude,
            "longitude": market.longitude,
            "boundary_radius_km": market.boundary_radius_km,
            "population": market.population,
            "reference_population": market.reference_population,
            "provider_location_code": market.provider_location_code,
            "provider_location_name": market.provider_location_name,
            "resolution_metadata": market.resolution_metadata,
        },
        "public_data_prefilter": public_data_prefilter,
        "evidence_quality": evidence_quality,
        "data_freshness": freshness,
        "demand": demand,
        "serp_composition": {
            "queries": [snapshot.query for snapshot in serp_snapshots],
            "features_present": sorted(
                {feature for snapshot in serp_snapshots for feature in snapshot.features_present}
            ),
            "classification_counts": composition,
            "results": [
                {
                    "order": result.order,
                    "domain": result.domain,
                    "title": result.title,
                    "classification": result.classification,
                    "confidence": result.classification_confidence,
                    "matched_rules": result.matched_rules,
                }
                for result in serp_results[:30]
            ],
        },
        "competitors": {
            "count": len(competitors),
            "items": [
                {
                    "domain": competitor.domain,
                    "page_url": competitor.page_url,
                    "normalized_domain": competitor.normalized_domain,
                    "referring_domains": competitor.referring_domains,
                    "authority": competitor.authority,
                    "page_referring_domains": competitor.page_referring_domains,
                    "page_backlinks": competitor.page_backlinks,
                    "page_authority": competitor.page_authority,
                    "domain_referring_domains": competitor.domain_referring_domains,
                    "domain_backlinks": competitor.domain_backlinks,
                    "domain_authority": competitor.domain_authority,
                    "page_metrics_available": competitor.page_metrics_available,
                    "domain_metrics_available": competitor.domain_metrics_available,
                    "page_type": competitor.page_type,
                    "page_relevance_score": competitor.page_relevance_score,
                    "local_relevance": competitor.local_relevance,
                    "signals": competitor.relevance_signals,
                    "serp_observations": [
                        observation.model_dump(mode="json")
                        for observation in competitor.serp_observations
                    ],
                }
                for competitor in competitors
            ],
        },
        "providers": score.input_measurements.get("provider_suitability", {}),
        "score_breakdown": {
            "version": score.scoring_version,
            "evidence_status": score.evidence_status,
            "uncapped_total_score": score.uncapped_total_score,
            "score_cap": score.score_cap,
            "config_hash": score.scoring_config_hash,
            "components": score.component_scores,
            "component_explanations": score.component_explanations,
            "component_details": {
                component: detail.model_dump(mode="json")
                for component, detail in score.component_details.items()
            },
            "penalties": score.missing_data_penalties,
            "penalty_details": [
                {
                    "field": field,
                    "points": -points,
                    "detail": f"Missing {field.replace('_', ' ')} evidence.",
                }
                for field, points in score.missing_data_penalties.items()
            ],
            "missing_fields": score.missing_fields,
            "assumptions": score.assumptions,
            "confidence_model": score.input_measurements.get("confidence_model", {}),
        },
        "scan_metadata": scan_metadata,
    }


def build_api_cost_ledger(
    session: Session,
    scan_run_id: int | None,
) -> dict[str, Any]:
    if scan_run_id is None:
        rows: list[ApiCallORM] = []
        planned_rows: list[ScanPlanCallORM] = []
    else:
        rows = list(
            session.scalars(
                select(ApiCallORM)
                .where(ApiCallORM.scan_run_id == scan_run_id)
                .order_by(ApiCallORM.id)
            ).all()
        )
        planned_rows = list(
            session.scalars(
                select(ScanPlanCallORM)
                .where(ScanPlanCallORM.scan_run_id == scan_run_id)
                .order_by(ScanPlanCallORM.id)
            ).all()
        )
    terminal_statuses = {"cache_hit", "completed", "failed"}
    actual_cost = round(sum(row.actual_cost_usd or 0 for row in rows), 6)
    estimated_cost = round(
        sum(
            row.estimated_cost_usd or 0
            for row in (planned_rows if planned_rows else rows)
        ),
        6,
    )
    executed_by_plan_id: dict[str, ApiCallORM] = {
        row.planned_request_id: row
        for row in rows
        if row.planned_request_id is not None
    }
    planned_ids = {
        row.planned_request_id
        for row in planned_rows
        if row.planned_request_id is not None
    }
    unexecuted = [
        row
        for row in planned_rows
        if row.planned_request_id not in executed_by_plan_id
    ]
    unexpected = [
        row
        for row in rows
        if row.planned_request_id is None
        or row.planned_request_id not in planned_ids
    ] if planned_rows else []
    reconciled_calls = [
        _reconciled_call_payload(
            planned,
            executed_by_plan_id.get(planned.planned_request_id)
            if planned.planned_request_id is not None
            else None,
        )
        for planned in planned_rows
    ]
    reconciled_calls.extend(
        _reconciled_call_payload(None, executed)
        for executed in unexpected
    )
    if not planned_rows:
        reconciled_calls = [
            _reconciled_call_payload(None, executed) for executed in rows
        ]
    return {
        "scan_run_id": scan_run_id,
        "ledger_complete": (
            not unexecuted
            and not unexpected
            and all(row.status in terminal_statuses for row in rows)
        ),
        "call_count": len(rows),
        "planned_call_count": len(planned_rows),
        "executed_call_count": len(rows),
        "network_call_count": sum(
            not row.cache_hit and row.status in {"completed", "failed"}
            for row in rows
        ),
        "cache_hit_count": sum(row.cache_hit for row in rows),
        "failed_call_count": sum(row.status == "failed" for row in rows),
        "unexecuted_call_count": len(unexecuted),
        "unexpected_call_count": len(unexpected),
        "estimated_cost_usd": estimated_cost,
        "actual_cost_usd": actual_cost,
        "calls": reconciled_calls,
    }


def _reconciled_call_payload(
    planned: ScanPlanCallORM | None,
    executed: ApiCallORM | None,
) -> dict[str, Any]:
    return {
        "api_call_id": executed.id if executed else None,
        "planned_call_id": planned.id if planned else None,
        "planned_request_id": (
            planned.planned_request_id
            if planned
            else executed.planned_request_id
            if executed
            else None
        ),
        "provider": planned.provider if planned else executed.provider if executed else None,
        "endpoint": planned.endpoint if planned else executed.endpoint if executed else None,
        "stage": planned.stage if planned else executed.stage if executed else None,
        "planned_status": "planned" if planned else "unexpected",
        "execution_status": executed.status if executed else "not_executed",
        "status": executed.status if executed else "not_executed",
        "cache_hit": executed.cache_hit if executed else planned.cache_hit if planned else False,
        "required": planned.required if planned else None,
        "estimated_cost_usd": (
            planned.estimated_cost_usd
            if planned
            else executed.estimated_cost_usd
            if executed
            else 0
        ),
        "actual_cost_usd": executed.actual_cost_usd if executed else 0,
        "started_at": (
            executed.started_at.isoformat()
            if executed and executed.started_at
            else None
        ),
        "completed_at": (
            executed.completed_at.isoformat()
            if executed and executed.completed_at
            else None
        ),
        "provider_task_id": executed.provider_task_id if executed else None,
        "provider_request_id": executed.provider_request_id if executed else None,
        "error_type": executed.error_type if executed else None,
        "error_summary": executed.error_summary if executed else None,
    }


def _summary_status(score: OpportunityScore) -> str:
    if score.evidence_status == "unusable":
        return "unusable"
    if score.evidence_status in {"partial", "preliminary"}:
        return "needs_more_evidence"
    if score.confidence.value in {"low", "insufficient"}:
        return "needs_more_evidence"
    if score.total_score >= 70:
        return "strong_candidate"
    if score.total_score >= 50:
        return "worth_reviewing"
    return "weak_candidate"


def _composition(values: list[str]) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        output[value or "unknown"] = output.get(value or "unknown", 0) + 1
    return output


def _data_freshness(
    metrics: list[KeywordMetric],
    serp_snapshots: list[SerpSnapshot],
    providers: list[ProviderCandidate],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    groups = {
        "keyword_metrics": (
            [metric.source_timestamp for metric in metrics],
            90,
        ),
        "serp_snapshots": (
            [snapshot.captured_at for snapshot in serp_snapshots],
            30,
        ),
        "provider_candidates": (
            [provider.source_timestamp for provider in providers],
            90,
        ),
    }
    payloads = {
        name: _freshness_payload(timestamps, now, maximum_age_days)
        for name, (timestamps, maximum_age_days) in groups.items()
    }
    known = [payload for payload in payloads.values() if payload["status"] != "unknown"]
    stale_groups = [
        name for name, payload in payloads.items() if payload["status"] == "stale"
    ]
    aging_groups = [
        name for name, payload in payloads.items() if payload["status"] == "aging"
    ]
    overall_status = (
        "stale"
        if stale_groups
        else "aging"
        if aging_groups
        else "fresh"
        if known
        else "unknown"
    )
    return {
        "overall_status": overall_status,
        "as_of": now.isoformat(),
        "oldest_age_days": max(
            (
                float(payload["oldest_age_days"])
                for payload in known
                if payload["oldest_age_days"] is not None
            ),
            default=None,
        ),
        "stale_groups": stale_groups,
        "groups": payloads,
    }


def _freshness_payload(
    timestamps: list[datetime],
    now: datetime,
    maximum_age_days: int,
) -> dict[str, Any]:
    if not timestamps:
        return {
            "newest_at": None,
            "oldest_at": None,
            "newest_age_days": None,
            "oldest_age_days": None,
            "maximum_age_days": maximum_age_days,
            "status": "unknown",
        }
    normalized = [
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        for value in timestamps
    ]
    newest = max(normalized)
    oldest = min(normalized)
    oldest_age = max(0.0, (now - oldest).total_seconds() / 86_400)
    status = (
        "stale"
        if oldest_age > maximum_age_days
        else "aging"
        if oldest_age > maximum_age_days * 0.75
        else "fresh"
    )
    return {
        "newest_at": newest.isoformat(),
        "oldest_at": oldest.isoformat(),
        "newest_age_days": round(max(0.0, (now - newest).total_seconds() / 86_400), 2),
        "oldest_age_days": round(oldest_age, 2),
        "maximum_age_days": maximum_age_days,
        "status": status,
    }
