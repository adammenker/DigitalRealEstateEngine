from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, cast

import yaml

from rank_rent.domain.models import (
    CompetitorMetric,
    Confidence,
    KeywordMetric,
    Market,
    OpportunityScore,
    ProviderCandidate,
    ScoreCalculationStep,
    ScoreComponentDetail,
    SerpSnapshot,
)
from rank_rent.services.demand import analyze_demand
from rank_rent.services.providers import provider_suitability_summary

HIGH_INTENTS = {"transactional", "commercial"}
CONFIDENCE_RANK = {
    Confidence.insufficient: 0,
    Confidence.low: 1,
    Confidence.medium: 2,
    Confidence.high: 3,
}


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
        *,
        source_mode: str | None = None,
        assessment_type: str = "full",
    ) -> OpportunityScore:
        scored_metrics = [metric for metric in metrics if metric.included]
        market = market or Market(id="unknown", display_name="Unknown")
        demand_model = analyze_demand(scored_metrics, market)
        missing = self._missing_fields(
            scored_metrics,
            serp_snapshots,
            competitors,
            providers,
            demand_model,
        )

        high_intent = [metric for metric in scored_metrics if metric.intent in HIGH_INTENTS]
        avg_cpc = mean([metric.cpc for metric in scored_metrics if metric.cpc is not None] or [0])
        avg_paid_competition = mean(
            [metric.paid_competition for metric in scored_metrics if metric.paid_competition is not None]
            or [0]
        )

        demand, demand_score_inputs = self._demand_evidence(demand_model)
        demand_detail = self._demand_component_detail(demand, demand_score_inputs)
        commercial, commercial_detail = self._commercial_value(
            avg_cpc=avg_cpc,
            avg_paid_competition=avg_paid_competition,
            high_intent_count=len(high_intent),
            scored_keyword_count=len(scored_metrics),
        )

        competitor, competitor_detail = self._competitor_weakness(competitors)
        organic, organic_detail = self._organic_click_availability(serp_snapshots)
        provider, provider_detail = self._provider_suitability(providers)
        completeness, completeness_detail = self._data_completeness(missing)

        penalties = self._missing_data_penalties(missing)
        components = {
            "demand_evidence": demand,
            "commercial_value": commercial,
            "competitor_weakness": competitor,
            "organic_click_availability": organic,
            "provider_suitability": provider,
            "data_completeness": completeness,
        }
        component_details = {
            "demand_evidence": demand_detail,
            "commercial_value": commercial_detail,
            "competitor_weakness": competitor_detail,
            "organic_click_availability": organic_detail,
            "provider_suitability": provider_detail,
            "data_completeness": completeness_detail,
        }
        total = sum(components.values()) - sum(penalties.values())
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
        measurements["demand_score_inputs"] = demand_score_inputs
        confidence, confidence_model = self._confidence(
            missing=missing,
            metrics=scored_metrics,
            serp_snapshots=serp_snapshots,
            competitors=competitors,
            providers=providers,
            demand_model=demand_model,
            source_mode=source_mode,
            assessment_type=assessment_type,
        )
        measurements["confidence_model"] = confidence_model
        explanations = {
            component: detail.explanation
            for component, detail in component_details.items()
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
            component_details=component_details,
            missing_fields=missing,
            assumptions=assumptions,
        )

    def _demand_evidence(self, demand_model: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        weight = self.weights["demand_evidence"]
        config = self.config["demand"]
        service_share = max(0.0, float(config["service_attractiveness_share"]))
        market_share = max(0.0, float(config["market_attractiveness_share"]))
        total_share = service_share + market_share
        if total_share <= 0:
            return 0.0, {
                "service_score": 0.0,
                "service_weight": 0.0,
                "market_score": 0.0,
                "market_weight": 0.0,
            }
        service_share /= total_share
        market_share /= total_share
        service_weight = weight * service_share
        market_weight = weight * market_share
        service_demand = float(demand_model["service_attractiveness_demand"] or 0)
        market_demand = float(demand_model["estimated_market_demand"] or 0)
        service_threshold_key = (
            "strong_national_monthly_volume"
            if demand_model["national_service_demand"] is not None
            else "strong_market_monthly_volume"
        )
        service_threshold = max(1.0, float(config[service_threshold_key]))
        market_threshold = max(1.0, float(config["strong_market_monthly_volume"]))
        service_score = _bounded(
            (service_demand / service_threshold) * service_weight,
            service_weight,
        )
        market_score = _bounded(
            (market_demand / market_threshold) * market_weight,
            market_weight,
        )
        return _bounded(service_score + market_score, weight), {
            "service_attractiveness_demand": service_demand or None,
            "service_threshold": service_threshold,
            "service_weight": round(service_weight, 2),
            "service_score": service_score,
            "market_attractiveness_demand": market_demand or None,
            "market_threshold": market_threshold,
            "market_weight": round(market_weight, 2),
            "market_score": market_score,
            "market_demand_kind": demand_model["market_demand_kind"],
            "market_estimation_confidence": demand_model["market_estimation_confidence"],
        }

    def _demand_component_detail(
        self,
        score: float,
        inputs: dict[str, Any],
    ) -> ScoreComponentDetail:
        market_demand = inputs["market_attractiveness_demand"]
        market_text = (
            f"{float(market_demand):g} market-level monthly searches"
            if isinstance(market_demand, int | float)
            else "no market-level demand measurement"
        )
        explanation = (
            f"{float(inputs['service_attractiveness_demand'] or 0):g} monthly searches "
            f"support service attractiveness, with {market_text}. "
            f"The evidence is classified as {inputs['market_demand_kind']} "
            f"with {inputs['market_estimation_confidence']} estimation confidence."
        )
        return ScoreComponentDetail(
            score=score,
            maximum_score=self.weights["demand_evidence"],
            formula=(
                "clamp(service_demand / service_threshold * service_weight, 0, "
                "service_weight) + clamp(market_demand / market_threshold * "
                "market_weight, 0, market_weight)"
            ),
            inputs=inputs,
            calculation_steps=[
                ScoreCalculationStep(
                    label="Service attractiveness",
                    points=float(inputs["service_score"]),
                    detail=(
                        f"{float(inputs['service_attractiveness_demand'] or 0):g} searches "
                        f"against a strong-demand threshold of "
                        f"{float(inputs['service_threshold']):g}."
                    ),
                    inputs={
                        "demand": inputs["service_attractiveness_demand"],
                        "threshold": inputs["service_threshold"],
                        "maximum_points": inputs["service_weight"],
                    },
                ),
                ScoreCalculationStep(
                    label="Market attractiveness",
                    points=float(inputs["market_score"]),
                    detail=(
                        f"{market_text.capitalize()} against a strong-market threshold "
                        f"of {float(inputs['market_threshold']):g}."
                    ),
                    inputs={
                        "demand": market_demand,
                        "threshold": inputs["market_threshold"],
                        "maximum_points": inputs["market_weight"],
                        "demand_kind": inputs["market_demand_kind"],
                    },
                ),
            ],
            explanation=explanation,
        )

    def _commercial_value(
        self,
        *,
        avg_cpc: float,
        avg_paid_competition: float,
        high_intent_count: int,
        scored_keyword_count: int,
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["commercial_value"]
        config = self.config["commercial"]
        strong_cpc = max(1.0, float(config["strong_cpc"]))
        strong_paid_competition = max(
            0.01,
            float(config["strong_paid_competition"]),
        )
        high_intent_share = high_intent_count / max(1, scored_keyword_count)
        cpc_points = (avg_cpc / strong_cpc) * 9
        competition_points = (
            avg_paid_competition / strong_paid_competition
        ) * 3
        intent_points = high_intent_share * 4
        raw_score = cpc_points + competition_points + intent_points
        score = _bounded(raw_score, weight)
        steps = [
            ScoreCalculationStep(
                label="Advertiser value",
                points=round(cpc_points, 4),
                detail=(
                    f"Average CPC ${avg_cpc:.2f} against the ${strong_cpc:.2f} "
                    "strong-CPC threshold."
                ),
                inputs={
                    "average_cpc": round(avg_cpc, 4),
                    "strong_cpc": strong_cpc,
                    "maximum_points": 9,
                },
            ),
            ScoreCalculationStep(
                label="Paid competition",
                points=round(competition_points, 4),
                detail=(
                    f"Average paid competition {avg_paid_competition:.3f} against "
                    f"the {strong_paid_competition:.3f} threshold."
                ),
                inputs={
                    "average_paid_competition": round(
                        avg_paid_competition,
                        4,
                    ),
                    "strong_paid_competition": strong_paid_competition,
                    "maximum_points": 3,
                },
            ),
            ScoreCalculationStep(
                label="Commercial intent",
                points=round(intent_points, 4),
                detail=(
                    f"{high_intent_count} of {scored_keyword_count} scored keywords "
                    f"are commercial or transactional ({high_intent_share:.0%})."
                ),
                inputs={
                    "high_intent_keyword_count": high_intent_count,
                    "scored_keyword_count": scored_keyword_count,
                    "high_intent_share": round(high_intent_share, 4),
                    "maximum_points": 4,
                },
            ),
        ]
        bounded_raw_score = max(0.0, min(weight, raw_score))
        cap_adjustment = bounded_raw_score - raw_score
        if abs(cap_adjustment) >= 0.0001:
            steps.append(
                ScoreCalculationStep(
                    label="Component cap",
                    points=round(cap_adjustment, 4),
                    detail=f"Raw commercial evidence was capped at {weight:g} points.",
                    inputs={"raw_score": round(raw_score, 4), "cap": weight},
                )
            )
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                f"clamp((average_cpc / {strong_cpc:g}) * 9 + "
                f"(average_paid_competition / {strong_paid_competition:g}) * 3 + "
                f"high_intent_share * 4, 0, {weight:g})"
            ),
            inputs={
                "average_cpc": round(avg_cpc, 4),
                "strong_cpc": strong_cpc,
                "average_paid_competition": round(avg_paid_competition, 4),
                "strong_paid_competition": strong_paid_competition,
                "high_intent_keyword_count": high_intent_count,
                "scored_keyword_count": scored_keyword_count,
                "high_intent_share": round(high_intent_share, 4),
            },
            calculation_steps=steps,
            explanation=(
                f"Average CPC is ${avg_cpc:.2f}, paid competition averages "
                f"{avg_paid_competition:.3f}, and {high_intent_count} of "
                f"{scored_keyword_count} scored keywords have high commercial intent."
            ),
        )

    def _competitor_weakness(
        self,
        competitors: list[CompetitorMetric],
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["competitor_weakness"]
        if not competitors:
            return 0.0, ScoreComponentDetail(
                score=0,
                maximum_score=weight,
                formula=(
                    "mean(clamp(link_weakness * (1 - relevance_threat_strength * "
                    "direct_relevance) + archetype_adjustment, 0, 1)) * "
                    f"{weight:g}"
                ),
                inputs={"competitor_count": 0},
                calculation_steps=[
                    ScoreCalculationStep(
                        label="Missing competitor evidence",
                        points=0,
                        detail="No competitor pages were available to underwrite.",
                    )
                ],
                explanation="No competitor evidence was available, so this component received 0 points.",
            )
        config = self.config["competitors"]
        weak_ref = float(config["weak_referring_domains"])
        strong_ref = float(config["strong_referring_domains"])
        relevance_threat_strength = float(config["relevance_threat_strength"])
        archetype_adjustments = config["archetype_weakness_adjustments"]
        values: list[float] = []
        competitor_inputs: list[dict[str, Any]] = []
        archetype_counts: dict[str, int] = {}
        base_total = 0.0
        relevance_deduction_total = 0.0
        archetype_point_totals: dict[str, float] = {}
        unclamped_total = 0.0
        for competitor in competitors:
            ref_domains = competitor.referring_domains
            if ref_domains is None:
                base = float(config["unknown_referring_domains_weakness"])
            else:
                base = 1 - min(max(ref_domains - weak_ref, 0), strong_ref - weak_ref) / max(1, strong_ref - weak_ref)
            page_relevance = (
                competitor.page_relevance_score
                if competitor.page_relevance_score is not None
                else 0.5
            )
            relevance = max(page_relevance, competitor.local_relevance or 0.0)
            relevance = max(0.0, min(1.0, relevance))
            direct_threat_penalty = relevance_threat_strength * relevance
            archetype = str(
                competitor.relevance_signals.get("competitor_archetype")
                or competitor.page_type
                or "unknown"
            )
            archetype_adjustment = float(archetype_adjustments.get(archetype, 0))
            relevance_deduction = -(base * direct_threat_penalty)
            raw_value = base + relevance_deduction + archetype_adjustment
            normalized_value = max(0, min(1, raw_value))
            values.append(normalized_value)
            base_total += base
            relevance_deduction_total += relevance_deduction
            archetype_point_totals[archetype] = (
                archetype_point_totals.get(archetype, 0) + archetype_adjustment
            )
            archetype_counts[archetype] = archetype_counts.get(archetype, 0) + 1
            unclamped_total += raw_value
            competitor_inputs.append(
                {
                    "domain": competitor.domain,
                    "referring_domains": ref_domains,
                    "link_weakness": round(base, 4),
                    "direct_relevance": round(relevance, 4),
                    "relevance_deduction": round(relevance_deduction, 4),
                    "archetype": archetype,
                    "archetype_adjustment": archetype_adjustment,
                    "unclamped_weakness": round(raw_value, 4),
                    "normalized_weakness": round(normalized_value, 4),
                    "component_points": round(
                        normalized_value * weight / len(competitors),
                        4,
                    ),
                }
            )
        count = len(competitors)
        score = _bounded(mean(values) * weight, weight)
        measured_referring_domains = [
            competitor.referring_domains
            for competitor in competitors
            if competitor.referring_domains is not None
        ]
        median_referring_domains = (
            float(median(measured_referring_domains))
            if measured_referring_domains
            else None
        )
        average_relevance = mean(
            item["direct_relevance"] for item in competitor_inputs
        )
        high_relevance_count = sum(
            item["direct_relevance"] >= 0.65 for item in competitor_inputs
        )
        base_points = base_total / count * weight
        relevance_points = relevance_deduction_total / count * weight
        steps = [
            ScoreCalculationStep(
                label="Link weakness baseline",
                points=round(base_points, 4),
                detail=(
                    f"Median referring domains: {median_referring_domains:g} across "
                    f"{len(measured_referring_domains)} measured competitors."
                    if median_referring_domains is not None
                    else (
                        f"Referring domains were unavailable for all {count} competitors; "
                        "the configured unknown-authority baseline was used."
                    )
                ),
                inputs={
                    "median_referring_domains": median_referring_domains,
                    "measured_competitor_count": len(measured_referring_domains),
                    "unknown_competitor_count": count
                    - len(measured_referring_domains),
                    "weak_referring_domains": weak_ref,
                    "strong_referring_domains": strong_ref,
                },
            ),
            ScoreCalculationStep(
                label="Direct relevance threat",
                points=round(relevance_points, 4),
                detail=(
                    f"{high_relevance_count} of {count} pages have direct relevance "
                    f"of at least 0.65; average relevance is {average_relevance:.2f}."
                ),
                inputs={
                    "high_relevance_competitor_count": high_relevance_count,
                    "competitor_count": count,
                    "average_direct_relevance": round(average_relevance, 4),
                    "relevance_threat_strength": relevance_threat_strength,
                },
            ),
        ]
        for archetype in sorted(archetype_point_totals):
            normalized_adjustment = archetype_point_totals[archetype]
            if abs(normalized_adjustment) < 0.0001:
                continue
            points = normalized_adjustment / count * weight
            per_result = float(archetype_adjustments.get(archetype, 0))
            steps.append(
                ScoreCalculationStep(
                    label=f"{archetype.replace('_', ' ').title()} adjustment",
                    points=round(points, 4),
                    detail=(
                        f"{archetype_counts[archetype]} {archetype.replace('_', ' ')} "
                        f"result(s) at {per_result:+.2f} normalized weakness each."
                    ),
                    inputs={
                        "archetype": archetype,
                        "result_count": archetype_counts[archetype],
                        "adjustment_per_result": per_result,
                    },
                )
            )
        clamp_points = (
            (sum(values) - unclamped_total) / count * weight
        )
        if abs(clamp_points) >= 0.0001:
            steps.append(
                ScoreCalculationStep(
                    label="Boundary adjustment",
                    points=round(clamp_points, 4),
                    detail="Per-page weakness values were clamped to the 0-to-1 range.",
                    inputs={"minimum": 0, "maximum": 1},
                )
            )
        archetype_summary = ", ".join(
            f"{count_value} {archetype.replace('_', ' ')}"
            for archetype, count_value in sorted(archetype_counts.items())
        )
        median_text = (
            f"{median_referring_domains:g}"
            if median_referring_domains is not None
            else "unavailable"
        )
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                "mean(clamp(link_weakness * (1 - "
                f"{relevance_threat_strength:g} * direct_relevance) + "
                f"archetype_adjustment, 0, 1)) * {weight:g}"
            ),
            inputs={
                "competitor_count": count,
                "median_referring_domains": median_referring_domains,
                "average_direct_relevance": round(average_relevance, 4),
                "high_relevance_competitor_count": high_relevance_count,
                "archetype_counts": archetype_counts,
                "weak_referring_domains": weak_ref,
                "strong_referring_domains": strong_ref,
                "unknown_referring_domains_weakness": float(
                    config["unknown_referring_domains_weakness"]
                ),
                "relevance_threat_strength": relevance_threat_strength,
                "archetype_weakness_adjustments": archetype_adjustments,
                "per_competitor": competitor_inputs,
            },
            calculation_steps=steps,
            explanation=(
                f"Median referring domains are {median_text}; {high_relevance_count} of "
                f"{count} competitors are strongly relevant. Observed archetypes: "
                f"{archetype_summary}."
            ),
        )

    def _organic_click_availability(
        self,
        serp_snapshots: list[SerpSnapshot],
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["organic_click_availability"]
        results = [result for snapshot in serp_snapshots for result in snapshot.results]
        if not results:
            return 0.0, ScoreComponentDetail(
                score=0,
                maximum_score=weight,
                formula=(
                    f"clamp((1 - result_displacement - feature_displacement) * "
                    f"{weight:g}, 0, {weight:g})"
                ),
                inputs={
                    "serp_snapshot_count": len(serp_snapshots),
                    "serp_result_count": 0,
                },
                calculation_steps=[
                    ScoreCalculationStep(
                        label="Missing SERP evidence",
                        points=0,
                        detail="No SERP results were available to underwrite click availability.",
                    )
                ],
                explanation="No SERP results were available, so this component received 0 points.",
            )
        penalties = self.config["organic_click"]
        classification_penalties = {
            "directory": float(penalties["directory_penalty"]),
            "marketplace": float(penalties["marketplace_penalty"]),
            "national_brand": float(penalties["national_brand_penalty"]),
            "lead_generator": float(penalties["lead_generator_penalty"]),
            "informational_publisher": float(penalties["informational_publisher_penalty"]),
        }
        shopping_types = {
            str(result_type).lower()
            for result_type in penalties.get("shopping_product_result_types", [])
        }
        shopping_snapshot_share = (
            sum(
                1
                for snapshot in serp_snapshots
                if shopping_types
                & {
                    *(feature.lower() for feature in snapshot.features_present),
                    *(result.result_type.lower() for result in snapshot.results),
                }
            )
            / len(serp_snapshots)
        )
        local_pack = any("local_pack" in snapshot.features_present for snapshot in serp_snapshots)
        ads_top = any("ads_top" in snapshot.features_present for snapshot in serp_snapshots)
        availability = 1.0
        classification_counts = _serp_composition(results)
        steps = [
            ScoreCalculationStep(
                label="Available organic-click baseline",
                points=weight,
                detail=f"Started with the full {weight:g}-point click-availability allowance.",
                inputs={"maximum_points": weight},
            )
        ]
        for classification, penalty in classification_penalties.items():
            result_count = classification_counts.get(classification, 0)
            result_share = result_count / len(results)
            deduction = result_share * penalty
            availability -= deduction
            if result_count:
                steps.append(
                    ScoreCalculationStep(
                        label=f"{classification.replace('_', ' ').title()} displacement",
                        points=round(-(deduction * weight), 4),
                        detail=(
                            f"{result_count} of {len(results)} results are "
                            f"{classification.replace('_', ' ')} pages."
                        ),
                        inputs={
                            "result_count": result_count,
                            "result_share": round(result_share, 4),
                            "penalty_rate": penalty,
                        },
                    )
                )
        shopping_penalty = float(penalties["shopping_product_penalty"])
        shopping_deduction = shopping_snapshot_share * shopping_penalty
        availability -= shopping_deduction
        if shopping_snapshot_share:
            steps.append(
                ScoreCalculationStep(
                    label="Shopping/product displacement",
                    points=round(-(shopping_deduction * weight), 4),
                    detail=(
                        f"Shopping or product features appear in "
                        f"{shopping_snapshot_share:.0%} of sampled SERPs."
                    ),
                    inputs={
                        "snapshot_share": round(shopping_snapshot_share, 4),
                        "penalty_rate": shopping_penalty,
                    },
                )
            )
        if local_pack:
            local_pack_deduction = float(penalties["local_pack_penalty"])
            availability -= local_pack_deduction
            steps.append(
                ScoreCalculationStep(
                    label="Local-pack displacement",
                    points=round(-(local_pack_deduction * weight), 4),
                    detail="At least one sampled SERP contains a local pack.",
                    inputs={"penalty_rate": local_pack_deduction},
                )
            )
        if ads_top:
            ads_deduction = float(penalties["ads_top_penalty"])
            availability -= ads_deduction
            steps.append(
                ScoreCalculationStep(
                    label="Top-ad displacement",
                    points=round(-(ads_deduction * weight), 4),
                    detail="At least one sampled SERP contains ads above organic results.",
                    inputs={"penalty_rate": ads_deduction},
                )
            )
        score = _bounded(availability * weight, weight)
        unclamped_score = availability * weight
        bounded_unrounded_score = max(0.0, min(weight, unclamped_score))
        cap_adjustment = bounded_unrounded_score - unclamped_score
        if abs(cap_adjustment) >= 0.0001:
            steps.append(
                ScoreCalculationStep(
                    label="Component boundary",
                    points=round(cap_adjustment, 4),
                    detail=f"Click availability was clamped to 0-{weight:g} points.",
                    inputs={"unclamped_score": round(unclamped_score, 4)},
                )
            )
        composition_text = ", ".join(
            f"{count} {classification.replace('_', ' ')}"
            for classification, count in sorted(classification_counts.items())
        )
        active_features = []
        if local_pack:
            active_features.append("local pack")
        if ads_top:
            active_features.append("top ads")
        if shopping_snapshot_share:
            active_features.append("shopping/product features")
        feature_text = ", ".join(active_features) if active_features else "none"
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                f"clamp((1 - sum(result_share * classification_penalty) - "
                f"shopping_serp_share * {shopping_penalty:g} - "
                f"local_pack_present * {float(penalties['local_pack_penalty']):g} - "
                f"ads_top_present * {float(penalties['ads_top_penalty']):g}) * "
                f"{weight:g}, 0, {weight:g})"
            ),
            inputs={
                "serp_snapshot_count": len(serp_snapshots),
                "serp_result_count": len(results),
                "classification_counts": classification_counts,
                "classification_penalties": classification_penalties,
                "shopping_snapshot_share": round(shopping_snapshot_share, 4),
                "shopping_product_penalty": shopping_penalty,
                "local_pack_present": local_pack,
                "local_pack_penalty": float(penalties["local_pack_penalty"]),
                "ads_top_present": ads_top,
                "ads_top_penalty": float(penalties["ads_top_penalty"]),
            },
            calculation_steps=steps,
            explanation=(
                f"Across {len(results)} results, the mix is {composition_text}. "
                f"Observed displacement features: {feature_text}."
            ),
        )

    def _provider_suitability(
        self,
        providers: list[ProviderCandidate],
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["provider_suitability"]
        if not providers:
            return 0.0, ScoreComponentDetail(
                score=0,
                maximum_score=weight,
                formula=(
                    f"clamp((supply_fit * 0.65 + average_quality * 0.35) * "
                    f"{weight:g}, 0, {weight:g})"
                ),
                inputs={"provider_count": 0, "suitable_provider_count": 0},
                calculation_steps=[
                    ScoreCalculationStep(
                        label="Missing provider evidence",
                        points=0,
                        detail="No provider candidates were available to underwrite.",
                    )
                ],
                explanation="No provider candidates were available, so this component received 0 points.",
            )
        summary = provider_suitability_summary(providers, self.config["providers"])
        suitable = cast(int, summary["suitable_provider_count"])
        average_score = cast(float, summary["average_suitability_score"])
        suitable_threshold = cast(float, summary["suitable_threshold"])
        ideal_min = int(self.config["providers"]["ideal_min"])
        ideal_max = int(self.config["providers"]["ideal_max"])
        oversupply = int(self.config["providers"]["oversupply_count"])
        base_supply_fit = min(suitable / max(1, ideal_min), 1.0)
        supply_multiplier = 1.0
        if len(providers) > oversupply:
            supply_multiplier = 0.72
        elif suitable > ideal_max:
            supply_multiplier = 0.88
        supply_fit = base_supply_fit * supply_multiplier
        quality = average_score / 100
        supply_points_before_adjustment = base_supply_fit * 0.65 * weight
        supply_adjustment_points = (
            supply_fit - base_supply_fit
        ) * 0.65 * weight
        quality_points = quality * 0.35 * weight
        score = _bounded(
            supply_points_before_adjustment
            + supply_adjustment_points
            + quality_points,
            weight,
        )
        steps = [
            ScoreCalculationStep(
                label="Suitable provider supply",
                points=round(supply_points_before_adjustment, 4),
                detail=(
                    f"{suitable} of {len(providers)} providers meet the "
                    f"{suitable_threshold:g}-point suitability threshold; "
                    f"the target minimum is {ideal_min}."
                ),
                inputs={
                    "provider_count": len(providers),
                    "suitable_provider_count": suitable,
                    "suitable_threshold": suitable_threshold,
                    "ideal_min": ideal_min,
                    "supply_fit_before_adjustment": round(base_supply_fit, 4),
                },
            )
        ]
        if abs(supply_adjustment_points) >= 0.0001:
            reason = (
                f"{len(providers)} candidates exceed the {oversupply}-provider "
                "oversupply threshold."
                if len(providers) > oversupply
                else (
                    f"{suitable} suitable providers exceed the preferred maximum "
                    f"of {ideal_max}."
                )
            )
            steps.append(
                ScoreCalculationStep(
                    label="Supply saturation adjustment",
                    points=round(supply_adjustment_points, 4),
                    detail=reason,
                    inputs={
                        "supply_multiplier": supply_multiplier,
                        "ideal_max": ideal_max,
                        "oversupply_count": oversupply,
                    },
                )
            )
        steps.append(
            ScoreCalculationStep(
                label="Average provider quality",
                points=round(quality_points, 4),
                detail=(
                    f"Average provider suitability is {average_score:.2f} out of 100."
                ),
                inputs={
                    "average_suitability_score": average_score,
                    "quality_factor": round(quality, 4),
                },
            )
        )
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                f"clamp((adjusted_supply_fit * 0.65 + "
                f"(average_suitability_score / 100) * 0.35) * "
                f"{weight:g}, 0, {weight:g})"
            ),
            inputs={
                **summary,
                "ideal_min": ideal_min,
                "ideal_max": ideal_max,
                "oversupply_count": oversupply,
                "supply_fit_before_adjustment": round(base_supply_fit, 4),
                "supply_multiplier": supply_multiplier,
                "adjusted_supply_fit": round(supply_fit, 4),
            },
            calculation_steps=steps,
            explanation=(
                f"{suitable} of {len(providers)} providers meet the configured "
                f"suitability threshold, with an average score of {average_score:.2f}. "
                f"The supply multiplier is {supply_multiplier:.2f}."
            ),
        )

    def _data_completeness(
        self,
        missing: list[str],
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["data_completeness"]
        expected_groups = max(1, int(self.config.get("data_completeness_expected_groups", 7)))
        raw_score = weight * (1 - len(missing) / expected_groups)
        score = _bounded(raw_score, weight)
        missing_deduction = -(weight * len(missing) / expected_groups)
        steps = [
            ScoreCalculationStep(
                label="Expected evidence baseline",
                points=weight,
                detail=f"The model expects {expected_groups} evidence groups.",
                inputs={
                    "expected_evidence_group_count": expected_groups,
                    "maximum_points": weight,
                },
            ),
            ScoreCalculationStep(
                label="Missing evidence groups",
                points=round(missing_deduction, 4),
                detail=(
                    f"{len(missing)} groups are missing: "
                    f"{', '.join(missing) if missing else 'none'}."
                ),
                inputs={"missing_fields": missing, "missing_count": len(missing)},
            ),
        ]
        bounded_raw_score = max(0.0, min(weight, raw_score))
        cap_adjustment = bounded_raw_score - raw_score
        if abs(cap_adjustment) >= 0.0001:
            steps.append(
                ScoreCalculationStep(
                    label="Component boundary",
                    points=round(cap_adjustment, 4),
                    detail=f"Completeness was clamped to 0-{weight:g} points.",
                    inputs={"unclamped_score": round(raw_score, 4)},
                )
            )
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                f"clamp({weight:g} * (1 - missing_group_count / "
                f"{expected_groups}), 0, {weight:g})"
            ),
            inputs={
                "missing_fields": missing,
                "missing_group_count": len(missing),
                "expected_evidence_group_count": expected_groups,
            },
            calculation_steps=steps,
            explanation=(
                f"{len(missing)} of {expected_groups} expected evidence groups are "
                f"missing: {', '.join(missing) if missing else 'none'}."
            ),
        )

    def _missing_fields(
        self,
        metrics: list[KeywordMetric],
        serp_snapshots: list[SerpSnapshot],
        competitors: list[CompetitorMetric],
        providers: list[ProviderCandidate],
        demand_model: dict[str, Any],
    ) -> list[str]:
        missing: list[str] = []
        if not metrics:
            missing.append("keyword_metrics")
        if demand_model["estimated_market_demand"] is None:
            missing.append("local_demand")
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

    def _confidence(
        self,
        *,
        missing: list[str],
        metrics: list[KeywordMetric],
        serp_snapshots: list[SerpSnapshot],
        competitors: list[CompetitorMetric],
        providers: list[ProviderCandidate],
        demand_model: dict[str, Any],
        source_mode: str | None,
        assessment_type: str,
    ) -> tuple[Confidence, dict[str, Any]]:
        config = self.config["confidence"]
        deductions_config = config["deductions"]
        score = 100.0
        deductions: list[dict[str, Any]] = []
        caps: list[dict[str, str]] = []

        def deduct(factor: str, points: float, detail: str) -> None:
            nonlocal score
            points = max(0.0, float(points))
            if points <= 0:
                return
            score -= points
            deductions.append(
                {"factor": factor, "points": round(points, 2), "detail": detail}
            )

        def cap(level: Confidence, reason: str) -> None:
            caps.append({"level": level.value, "reason": reason})

        ordinary_missing = [field for field in missing if field != "local_demand"]
        if ordinary_missing:
            deduct(
                "missing_evidence",
                len(ordinary_missing) * float(deductions_config["missing_field"]),
                f"Missing evidence groups: {', '.join(ordinary_missing)}.",
            )

        market_demand = demand_model["estimated_market_demand"]
        estimation_confidence = str(demand_model["market_estimation_confidence"])
        if market_demand is None:
            deduct(
                "missing_local_demand",
                deductions_config["missing_local_demand"],
                "No measured or transparently estimated market-level demand is available.",
            )
            cap(Confidence.low, "Missing local demand prevents stronger market confidence.")
        elif estimation_confidence == "low":
            deduct(
                "low_confidence_market_estimate",
                deductions_config["low_confidence_market_estimate"],
                "Market demand is derived from a low-confidence estimate.",
            )
            cap(
                Confidence.medium,
                "Population-derived local demand cannot produce high confidence.",
            )
        elif estimation_confidence == "medium":
            deduct(
                "medium_confidence_market_evidence",
                deductions_config["medium_confidence_market_evidence"],
                "Market demand is measured at a broader local-market granularity.",
            )

        resolved_source_mode = _source_mode(metrics, source_mode)
        source_penalty = float(
            config["source_mode_penalties"].get(
                resolved_source_mode,
                config["source_mode_penalties"].get("unknown", 0),
            )
        )
        deduct(
            f"{resolved_source_mode}_source",
            source_penalty,
            f"Evidence source mode is {resolved_source_mode}.",
        )
        if resolved_source_mode == "fixture":
            cap(Confidence.low, "Synthetic fixture evidence cannot produce strong confidence.")
        elif resolved_source_mode == "sandbox":
            cap(Confidence.medium, "Sandbox evidence cannot produce high confidence.")

        keyword_age_days = _oldest_age_days(
            [metric.source_timestamp for metric in metrics]
        )
        serp_age_days = _oldest_age_days(
            [snapshot.captured_at for snapshot in serp_snapshots]
        )
        if (
            keyword_age_days is not None
            and keyword_age_days > float(config["keyword_max_age_days"])
        ):
            deduct(
                "stale_keyword_metrics",
                deductions_config["stale_keyword_metrics"],
                f"Oldest keyword metric is {keyword_age_days:.1f} days old.",
            )
            cap(Confidence.medium, "Stale keyword evidence prevents high confidence.")
        if serp_age_days is not None and serp_age_days > float(config["serp_max_age_days"]):
            deduct(
                "stale_serps",
                deductions_config["stale_serps"],
                f"Oldest SERP snapshot is {serp_age_days:.1f} days old.",
            )
            cap(Confidence.medium, "Stale SERP evidence prevents high confidence.")

        sample_specs = [
            (
                "limited_serp_sample",
                len(serp_snapshots),
                int(config["representative_serp_target"]),
            ),
            (
                "limited_competitor_sample",
                len(competitors),
                int(config["competitor_sample_target"]),
            ),
            (
                "limited_provider_sample",
                len(providers),
                int(config["provider_sample_target"]),
            ),
        ]
        for factor, actual, target in sample_specs:
            if target <= 0 or actual >= target:
                continue
            max_deduction = float(deductions_config[factor])
            deduction = max_deduction * ((target - actual) / target)
            deduct(
                factor,
                deduction,
                f"Observed {actual} records against a target of {target}.",
            )
            cap(
                Confidence.medium,
                f"An incomplete {factor.removeprefix('limited_').replace('_', ' ')} "
                "prevents high confidence.",
            )

        if "competitor_metrics" in missing:
            cap(Confidence.medium, "Missing competitor evidence prevents high confidence.")
        if assessment_type == "preliminary":
            cap(Confidence.medium, "Preliminary assessments cannot be high confidence.")

        score = round(max(0.0, min(100.0, score)), 2)
        thresholds = config["thresholds"]
        if score >= float(thresholds["high"]):
            confidence = Confidence.high
        elif score >= float(thresholds["medium"]):
            confidence = Confidence.medium
        elif score >= float(thresholds["low"]):
            confidence = Confidence.low
        else:
            confidence = Confidence.insufficient
        for item in caps:
            cap_level = Confidence(item["level"])
            if CONFIDENCE_RANK[confidence] > CONFIDENCE_RANK[cap_level]:
                confidence = cap_level

        return confidence, {
            "score": score,
            "level": confidence.value,
            "source_mode": resolved_source_mode,
            "assessment_type": assessment_type,
            "deductions": deductions,
            "caps": caps,
            "keyword_age_days": round(keyword_age_days, 2)
            if keyword_age_days is not None
            else None,
            "serp_age_days": round(serp_age_days, 2) if serp_age_days is not None else None,
            "sample_counts": {
                "representative_serps": len(serp_snapshots),
                "competitors": len(competitors),
                "providers": len(providers),
            },
            "market_demand_kind": demand_model["market_demand_kind"],
            "market_estimation_confidence": estimation_confidence,
        }

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
            "serp_result_type_composition": _serp_result_type_composition(serp_results),
            "provider_suitability": provider_suitability_summary(
                providers,
                self.config["providers"],
            ),
        }

    def _assumptions(
        self, metrics: list[KeywordMetric], demand_model: dict[str, Any]
    ) -> list[str]:
        if not metrics:
            return ["No keyword metrics were available."]
        assumptions: list[str] = []
        if any(metric.source.startswith("fixture") or "fixture" in metric.source for metric in metrics):
            assumptions.append(
                "Fixture data is synthetic and should only be used for workflow testing."
            )
        if demand_model["national_service_demand"] is not None:
            if demand_model["estimated_market_demand"] is None:
                assumptions.append(
                    "National keyword volume supports service attractiveness only; no "
                    "market-level demand value was available."
                )
            else:
                assumptions.append(
                    "Market demand is estimated from national keyword volume and population "
                    "share; it is not provider-measured city demand."
                )
        else:
            assumptions.append(
                "Provider-local keyword volume is treated as directional market evidence."
            )
        return assumptions


def _serp_composition(results: list[Any]) -> dict[str, int]:
    composition: dict[str, int] = {}
    for result in results:
        classification = str(getattr(result, "classification", "unknown") or "unknown")
        composition[classification] = composition.get(classification, 0) + 1
    return composition


def _serp_result_type_composition(results: list[Any]) -> dict[str, int]:
    composition: dict[str, int] = {}
    for result in results:
        result_type = str(getattr(result, "result_type", "unknown") or "unknown")
        composition[result_type] = composition.get(result_type, 0) + 1
    return composition


def _source_mode(metrics: list[KeywordMetric], explicit_mode: str | None) -> str:
    if explicit_mode:
        return explicit_mode.strip().lower()
    sources = {metric.source.lower() for metric in metrics}
    if any("fixture" in source for source in sources):
        return "fixture"
    if any("sandbox" in source for source in sources):
        return "sandbox"
    if any("replay" in source for source in sources):
        return "replay"
    return "unknown"


def _oldest_age_days(timestamps: list[datetime]) -> float | None:
    if not timestamps:
        return None
    now = datetime.now(UTC)
    ages = []
    for timestamp in timestamps:
        normalized = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
        ages.append(max(0.0, (now - normalized).total_seconds() / 86400))
    return max(ages)
