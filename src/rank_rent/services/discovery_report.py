from __future__ import annotations

from typing import Any

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
from rank_rent.services.providers import provider_suitability_summary


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
        "providers": provider_suitability_summary(providers),
        "score_breakdown": {
            "version": score.scoring_version,
            "config_hash": score.scoring_config_hash,
            "components": score.component_scores,
            "component_explanations": score.component_explanations,
            "penalties": score.missing_data_penalties,
            "missing_fields": score.missing_fields,
            "assumptions": score.assumptions,
        },
        "scan_metadata": scan_metadata,
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
