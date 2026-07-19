from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import ApiCallORM
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
) -> dict[str, Any]:
    demand = analyze_demand([metric for metric in metrics if metric.included], market)
    serp_results = [result for snapshot in serp_snapshots for result in snapshot.results]
    composition = _composition([result.classification for result in serp_results])
    return {
        "summary": {
            "service": service.display_name,
            "market": market.display_name,
            "score": score.total_score,
            "confidence": score.confidence.value,
            "status": _summary_status(score),
            "explanation": score.explanation,
        },
        "market_interpretation": {
            "input_market": market.display_name,
            "market_type": market.type.value,
            "provider_location_code": market.provider_location_code,
            "provider_location_name": market.provider_location_name,
            "resolution_metadata": market.resolution_metadata,
        },
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
                    "referring_domains": competitor.referring_domains,
                    "authority": competitor.authority,
                    "page_type": competitor.page_type,
                    "page_relevance_score": competitor.page_relevance_score,
                    "local_relevance": competitor.local_relevance,
                    "signals": competitor.relevance_signals,
                }
                for competitor in competitors
            ],
        },
        "providers": score.input_measurements.get("provider_suitability", {}),
        "score_breakdown": {
            "version": score.scoring_version,
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
    else:
        rows = list(
            session.scalars(
                select(ApiCallORM)
                .where(ApiCallORM.scan_run_id == scan_run_id)
                .order_by(ApiCallORM.id)
            ).all()
        )
    terminal_statuses = {"cache_hit", "completed", "failed"}
    actual_cost = round(sum(row.actual_cost_usd or 0 for row in rows), 6)
    estimated_cost = round(sum(row.estimated_cost_usd or 0 for row in rows), 6)
    return {
        "scan_run_id": scan_run_id,
        "ledger_complete": all(row.status in terminal_statuses for row in rows),
        "call_count": len(rows),
        "network_call_count": sum(
            not row.cache_hit and row.status in {"completed", "failed"}
            for row in rows
        ),
        "cache_hit_count": sum(row.cache_hit for row in rows),
        "failed_call_count": sum(row.status == "failed" for row in rows),
        "estimated_cost_usd": estimated_cost,
        "actual_cost_usd": actual_cost,
        "calls": [
            {
                "api_call_id": row.id,
                "planned_request_id": row.planned_request_id,
                "provider": row.provider,
                "endpoint": row.endpoint,
                "stage": row.stage,
                "status": row.status,
                "cache_hit": row.cache_hit,
                "estimated_cost_usd": row.estimated_cost_usd,
                "actual_cost_usd": row.actual_cost_usd,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat()
                if row.completed_at
                else None,
                "provider_task_id": row.provider_task_id,
                "provider_request_id": row.provider_request_id,
                "error_type": row.error_type,
                "error_summary": row.error_summary,
            }
            for row in rows
        ],
    }


def _summary_status(score: OpportunityScore) -> str:
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
