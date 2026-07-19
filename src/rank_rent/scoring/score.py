from __future__ import annotations

import hashlib
from pathlib import Path
from statistics import mean
from typing import Any, cast

import yaml

from rank_rent.domain.models import (
    CompetitorMetric,
    Confidence,
    KeywordMetric,
    Market,
    OpportunityScore,
    ProviderCandidate,
    SerpSnapshot,
)
from rank_rent.services.demand import analyze_demand
from rank_rent.services.providers import provider_suitability_summary

HIGH_INTENTS = {"transactional", "commercial"}


def _bounded(value: float, maximum: float) -> float:
    return round(max(0, min(maximum, value)), 2)


class OpportunityScorer:
    def __init__(self, config_path: Path = Path("config/scoring.yaml")) -> None:
        raw_config = config_path.read_text()
        self.config = yaml.safe_load(raw_config)
        self.config_hash = hashlib.sha256(raw_config.encode("utf-8")).hexdigest()[:16]
        self.weights: dict[str, float] = self.config["weights"]

    def score(
        self,
        metrics: list[KeywordMetric],
        serp_snapshots: list[SerpSnapshot],
        competitors: list[CompetitorMetric],
        providers: list[ProviderCandidate],
        market: Market | None = None,
    ) -> OpportunityScore:
        scored_metrics = [metric for metric in metrics if metric.included]
        market = market or Market(id="unknown", display_name="Unknown")
        demand_model = analyze_demand(scored_metrics, market)
        missing = self._missing_fields(scored_metrics, serp_snapshots, competitors, providers)

        total_volume = float(demand_model["raw_keyword_volume"] or 0)
        high_intent = [metric for metric in scored_metrics if metric.intent in HIGH_INTENTS]
        avg_cpc = mean([metric.cpc for metric in scored_metrics if metric.cpc is not None] or [0])
        avg_paid_competition = mean(
            [metric.paid_competition for metric in scored_metrics if metric.paid_competition is not None]
            or [0]
        )

        demand = _bounded(
            (total_volume / max(1, float(self.config["demand"]["strong_monthly_volume"])))
            * self.weights["demand_evidence"],
            self.weights["demand_evidence"],
        )
        high_intent_share = len(high_intent) / max(1, len(scored_metrics))
        commercial = _bounded(
            (avg_cpc / max(1, float(self.config["commercial"]["strong_cpc"]))) * 9
            + (avg_paid_competition / max(0.01, float(self.config["commercial"]["strong_paid_competition"]))) * 3
            + high_intent_share * 4,
            self.weights["commercial_value"],
        )

        competitor = self._competitor_weakness(competitors)
        organic = self._organic_click_availability(serp_snapshots)
        provider = self._provider_suitability(providers)
        completeness = self._data_completeness(missing)

        penalties = self._missing_data_penalties(missing)
        components = {
            "demand_evidence": demand,
            "commercial_value": commercial,
            "competitor_weakness": competitor,
            "organic_click_availability": organic,
            "provider_suitability": provider,
            "data_completeness": completeness,
        }
        total = sum(components.values()) - sum(penalties.values())
        confidence = self._confidence(missing)
        measurements = self._measurements(
            metrics=scored_metrics,
            all_metrics=metrics,
            demand_model=demand_model,
            competitors=competitors,
            serp_snapshots=serp_snapshots,
            providers=providers,
            avg_cpc=avg_cpc,
            avg_paid_competition=avg_paid_competition,
        )
        explanations = {
            "demand_evidence": f"{int(total_volume)} included monthly searches across {len(scored_metrics)} deduped keyword clusters.",
            "commercial_value": f"Average CPC ${avg_cpc:.2f}, paid competition {avg_paid_competition:.2f}, high-intent share {high_intent_share:.0%}.",
            "competitor_weakness": "Higher when visible competitors have low authority/referring domains or are aggregators.",
            "organic_click_availability": "Higher when the SERP has fewer ads, local packs, directories, lead generators, and national brands.",
            "provider_suitability": "Higher when there are contactable local providers without severe oversupply.",
            "data_completeness": f"{len(missing)} missing evidence groups.",
        }
        explanation = (
            f"Score {round(max(0, total), 1)} combines demand, commercial value, competitor weakness, "
            f"organic click availability, provider suitability, and data completeness. It is discovery evidence, not a profit forecast."
        )
        assumptions = self._assumptions(metrics, demand_model)
        return OpportunityScore(
            total_score=round(max(0, total), 2),
            component_scores=components,
            input_measurements=measurements,
            missing_data_penalties=penalties,
            scoring_version=str(self.config["version"]),
            scoring_config_hash=self.config_hash,
            explanation=explanation,
            confidence=confidence,
            component_explanations=explanations,
            missing_fields=missing,
            assumptions=assumptions,
        )

    def _competitor_weakness(self, competitors: list[CompetitorMetric]) -> float:
        weight = self.weights["competitor_weakness"]
        if not competitors:
            return 0.0
        weak_ref = float(self.config["competitors"]["weak_referring_domains"])
        strong_ref = float(self.config["competitors"]["strong_referring_domains"])
        values = []
        for competitor in competitors:
            ref_domains = competitor.referring_domains
            if ref_domains is None:
                base = 0.45
            else:
                base = 1 - min(max(ref_domains - weak_ref, 0), strong_ref - weak_ref) / max(1, strong_ref - weak_ref)
            relevance = max(competitor.page_relevance_score or 0.5, competitor.local_relevance or 0.0)
            aggregator_bonus = 0.15 if competitor.relevance_signals.get("is_aggregator") else 0
            values.append(max(0, min(1, base * max(0.35, relevance) + aggregator_bonus)))
        return _bounded(mean(values) * weight, weight)

    def _organic_click_availability(self, serp_snapshots: list[SerpSnapshot]) -> float:
        weight = self.weights["organic_click_availability"]
        results = [result for snapshot in serp_snapshots for result in snapshot.results]
        if not results:
            return 0.0
        penalties = self.config["organic_click"]
        directory_share = len([result for result in results if result.classification == "directory"]) / len(results)
        national_share = len([result for result in results if result.classification == "national_brand"]) / len(results)
        lead_share = len([result for result in results if result.classification == "lead_generator"]) / len(results)
        local_pack = any("local_pack" in snapshot.features_present for snapshot in serp_snapshots)
        ads_top = any("ads_top" in snapshot.features_present for snapshot in serp_snapshots)
        availability = 1.0
        availability -= directory_share * float(penalties["directory_penalty"])
        availability -= national_share * float(penalties["national_brand_penalty"])
        availability -= lead_share * float(penalties["lead_generator_penalty"])
        availability -= float(penalties["local_pack_penalty"]) if local_pack else 0
        availability -= float(penalties["ads_top_penalty"]) if ads_top else 0
        return _bounded(availability * weight, weight)

    def _provider_suitability(self, providers: list[ProviderCandidate]) -> float:
        weight = self.weights["provider_suitability"]
        if not providers:
            return 0.0
        summary = provider_suitability_summary(providers)
        suitable = cast(int, summary["suitable_provider_count"])
        average_score = cast(float, summary["average_suitability_score"])
        ideal_min = int(self.config["providers"]["ideal_min"])
        ideal_max = int(self.config["providers"]["ideal_max"])
        oversupply = int(self.config["providers"]["oversupply_count"])
        supply_fit = min(suitable / max(1, ideal_min), 1.0)
        if len(providers) > oversupply:
            supply_fit *= 0.72
        elif suitable > ideal_max:
            supply_fit *= 0.88
        quality = average_score / 100
        return _bounded((supply_fit * 0.65 + quality * 0.35) * weight, weight)

    def _data_completeness(self, missing: list[str]) -> float:
        weight = self.weights["data_completeness"]
        return _bounded(weight * (1 - len(missing) / 6), weight)

    def _missing_fields(
        self,
        metrics: list[KeywordMetric],
        serp_snapshots: list[SerpSnapshot],
        competitors: list[CompetitorMetric],
        providers: list[ProviderCandidate],
    ) -> list[str]:
        missing: list[str] = []
        if not metrics:
            missing.append("keyword_metrics")
        if not serp_snapshots:
            missing.append("serp_snapshots")
        if not competitors:
            missing.append("competitor_metrics")
        if not providers:
            missing.append("provider_candidates")
        if metrics and not any(metric.cpc is not None for metric in metrics):
            missing.append("keyword_cpc")
        if serp_snapshots and not any(snapshot.results for snapshot in serp_snapshots):
            missing.append("serp_results")
        return missing

    def _missing_data_penalties(self, missing: list[str]) -> dict[str, float]:
        if not missing:
            return {}
        max_penalty = float(self.config["missing_data_penalty_max"])
        penalty_each = min(max_penalty / max(1, len(missing)), max_penalty / 3)
        return {field: round(penalty_each, 2) for field in missing}

    def _confidence(self, missing: list[str]) -> Confidence:
        thresholds = self.config["thresholds"]
        if len(missing) >= int(thresholds["insufficient_confidence_missing_fields"]):
            return Confidence.insufficient
        if len(missing) > int(thresholds["medium_confidence_missing_fields"]):
            return Confidence.low
        if len(missing) > int(thresholds["high_confidence_missing_fields"]):
            return Confidence.medium
        return Confidence.high

    def _measurements(
        self,
        *,
        metrics: list[KeywordMetric],
        all_metrics: list[KeywordMetric],
        demand_model: dict[str, Any],
        competitors: list[CompetitorMetric],
        serp_snapshots: list[SerpSnapshot],
        providers: list[ProviderCandidate],
        avg_cpc: float,
        avg_paid_competition: float,
    ) -> dict[str, Any]:
        serp_results = [result for snapshot in serp_snapshots for result in snapshot.results]
        granularities = sorted({metric.market_granularity or "unknown" for metric in metrics})
        return {
            **demand_model,
            "deduplicated_search_volume": demand_model["raw_keyword_volume"],
            "keyword_metric_granularities": granularities,
            "raw_national_service_demand": demand_model["national_service_demand"],
            "scored_keyword_count": len(metrics),
            "excluded_keyword_metric_count": len(all_metrics) - len(metrics),
            "average_cpc": round(avg_cpc, 2),
            "average_paid_competition": round(avg_paid_competition, 3),
            "average_competitor_referring_domains": round(
                mean([item.referring_domains for item in competitors if item.referring_domains is not None] or [0]),
                2,
            ),
            "competitor_count": len(competitors),
            "serp_result_count": len(serp_results),
            "serp_composition": _serp_composition(serp_results),
            "provider_suitability": provider_suitability_summary(providers),
        }

    def _assumptions(
        self, metrics: list[KeywordMetric], demand_model: dict[str, Any]
    ) -> list[str]:
        if not metrics:
            return ["No keyword metrics were available."]
        if any(metric.source.startswith("fixture") or "fixture" in metric.source for metric in metrics):
            return ["Fixture data is synthetic and should only be used for workflow testing."]
        if demand_model["national_service_demand"] is not None:
            return [
                "Keyword volume is provider-reported at national granularity; local demand is estimated only when population metadata is available."
            ]
        return ["Provider keyword volume is treated as directional market evidence."]


def _serp_composition(results: list[Any]) -> dict[str, int]:
    composition: dict[str, int] = {}
    for result in results:
        classification = str(getattr(result, "classification", "unknown") or "unknown")
        composition[classification] = composition.get(classification, 0) + 1
    return composition
