from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from rank_rent.domain.models import (
    CompetitorMetric,
    Confidence,
    KeywordMetric,
    OpportunityScore,
    ProviderCandidate,
    SerpSnapshot,
)


def _bounded(value: float, maximum: float) -> float:
    return round(max(0, min(maximum, value)), 2)


class OpportunityScorer:
    def __init__(self, config_path: Path = Path("config/scoring.yaml")) -> None:
        self.config = yaml.safe_load(config_path.read_text())
        self.weights: dict[str, float] = self.config["weights"]

    def score(
        self,
        metrics: list[KeywordMetric],
        serp_snapshots: list[SerpSnapshot],
        competitors: list[CompetitorMetric],
        providers: list[ProviderCandidate],
    ) -> OpportunityScore:
        missing: list[str] = []
        if not metrics:
            missing.append("keyword_metrics")
        if not serp_snapshots:
            missing.append("serp_snapshots")
        if not competitors:
            missing.append("competitor_metrics")
        if not providers:
            missing.append("provider_candidates")

        total_volume = sum(m.search_volume or 0 for m in metrics)
        high_intent = [m for m in metrics if m.intent in {"transactional", "commercial"}]
        avg_cpc = mean([m.cpc for m in metrics if m.cpc is not None] or [0])

        demand = _bounded((total_volume / 900) * self.weights["demand"], self.weights["demand"])
        commercial = _bounded(
            (avg_cpc / 25) * 8 + (len(high_intent) / max(1, len(metrics))) * 7,
            self.weights["commercial_intent"],
        )

        avg_ref_domains = mean([c.referring_domains for c in competitors if c.referring_domains is not None] or [250])
        local_competitor_share = mean([c.local_relevance or 0 for c in competitors] or [0])
        organic = _bounded(
            self.weights["organic_accessibility"]
            - min(18, avg_ref_domains / 35)
            + local_competitor_share * 10,
            self.weights["organic_accessibility"],
        )

        serp_results = [r for s in serp_snapshots for r in s.results]
        directory_share = (
            len([r for r in serp_results if r.is_directory or r.is_national_brand]) / len(serp_results)
            if serp_results
            else 1
        )
        local_pack_bonus = 3 if any("local_pack" in s.features_present for s in serp_snapshots) else 0
        ads_penalty = 2 if any("ads_top" in s.features_present for s in serp_snapshots) else 0
        serp = _bounded(
            self.weights["serp_accessibility"] * (1 - directory_share) + local_pack_bonus - ads_penalty,
            self.weights["serp_accessibility"],
        )

        credible = [
            p
            for p in providers
            if p.business_status not in {"closed", "closed_forever", "temporarily_closed"}
            and (p.website or p.phone or p.contact_form_url)
        ]
        provider_supply = _bounded(
            min(len(credible), 8) / 8 * self.weights["provider_supply"],
            self.weights["provider_supply"],
        )
        if len(credible) > 14:
            provider_supply = max(4, provider_supply - 4)

        penalty_each = min(
            self.config["missing_data_penalty_max"],
            self.config["missing_data_penalty_max"] / max(1, len(missing)),
        )
        penalties = {field: penalty_each for field in missing}
        total = demand + commercial + organic + serp + provider_supply - sum(penalties.values())
        confidence = Confidence.high if len(missing) <= 1 else Confidence.medium
        if len(missing) > self.config["thresholds"]["medium_confidence_missing_fields"]:
            confidence = Confidence.low

        components = {
            "demand": demand,
            "commercial_intent": commercial,
            "organic_accessibility": organic,
            "serp_accessibility": serp,
            "provider_supply": provider_supply,
        }
        measurements: dict[str, Any] = {
            "deduplicated_search_volume": total_volume,
            "high_intent_keyword_count": len(high_intent),
            "average_cpc": round(avg_cpc, 2),
            "average_competitor_referring_domains": round(avg_ref_domains, 2),
            "provider_count": len(providers),
            "credible_provider_count": len(credible),
            "serp_result_count": len(serp_results),
        }
        live_sources = [m for m in metrics if not m.source.startswith("fixture") and "fixture" not in m.source]
        volume_label = "live monthly searches" if live_sources else "fixture monthly searches"
        assumption = (
            "Live provider data can mix country-level keyword metrics with city-level SERP/provider data."
            if live_sources
            else "Fixture adapter uses representative sample data."
        )
        explanation = (
            f"Score {round(total, 1)} reflects {total_volume} {volume_label}, "
            f"{len(high_intent)} high-intent terms, average CPC ${avg_cpc:.2f}, "
            f"and {len(credible)} contactable providers. It is not a ranking or profit guarantee."
        )
        return OpportunityScore(
            total_score=round(max(0, total), 2),
            component_scores=components,
            input_measurements=measurements,
            missing_data_penalties=penalties,
            scoring_version=self.config["version"],
            explanation=explanation,
            confidence=confidence,
            missing_fields=missing,
            assumptions=[assumption],
        )
