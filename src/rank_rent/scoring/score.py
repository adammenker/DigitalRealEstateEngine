from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, cast
from urllib.parse import urlparse

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
from rank_rent.services.demand_estimation import build_market_demand_estimator
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
        self.market_demand_estimator = build_market_demand_estimator(
            self.config.get("demand", {}).get("market_estimator")
        )

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
        demand_model = analyze_demand(
            scored_metrics,
            market,
            estimator=self.market_demand_estimator,
        )
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

        competitor, competitor_detail = self._competitor_weakness(
            competitors,
            scored_metrics,
        )
        organic, organic_detail = self._organic_click_availability(
            serp_snapshots,
            scored_metrics,
        )
        unknown_serp_share = float(
            organic_detail.inputs.get("classification_weighted_shares", {}).get(
                "unknown",
                0.0,
            )
        )
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
            unknown_serp_share=unknown_serp_share,
        )
        measurements["confidence_model"] = confidence_model
        evidence_status = self._evidence_status(missing, assessment_type)
        score_cap = self._missing_evidence_score_cap(missing, assessment_type)
        explanations = {
            component: detail.explanation
            for component, detail in component_details.items()
        }
        uncapped_total = round(max(0, total), 2)
        final_total = (
            round(min(uncapped_total, score_cap), 2)
            if score_cap is not None
            else uncapped_total
        )
        measurements["evidence_status"] = evidence_status
        measurements["score_cap_model"] = {
            "uncapped_total_score": uncapped_total,
            "score_cap": score_cap,
            "cap_deduction": round(final_total - uncapped_total, 2),
            "critical_missing_fields": [
                field
                for field in missing
                if field in self.config["missing_evidence_score_caps"]
            ],
        }
        explanation = (
            f"Score {final_total:.1f} combines demand, commercial value, competitor "
            "weakness, organic click availability, provider suitability, and data "
            f"completeness. Evidence status is {evidence_status}. It is discovery "
            "evidence, not a profit forecast."
        )
        assumptions = self._assumptions(metrics, demand_model)
        return OpportunityScore(
            total_score=final_total,
            uncapped_total_score=uncapped_total,
            evidence_status=evidence_status,
            score_cap=score_cap,
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
        service_threshold = max(
            1.0,
            float(config["national"]["strong_monthly_volume"]),
        )
        market_evidence_type = _market_demand_evidence_type(demand_model)
        market_profile = config[
            "measured_local"
            if market_evidence_type == "measured_local"
            else "population_estimated"
        ]
        market_threshold = max(
            0.01,
            float(market_profile["strong_monthly_volume"]),
        )
        market_maximum_credit = min(
            1.0,
            max(0.0, float(market_profile["maximum_component_credit"])),
        )
        market_score_cap = market_weight * market_maximum_credit
        service_score = _bounded(
            (service_demand / service_threshold) * service_weight,
            service_weight,
        )
        market_score = _bounded(
            (market_demand / market_threshold) * market_weight,
            market_score_cap,
        )
        return _bounded(service_score + market_score, weight), {
            "service_attractiveness_demand": service_demand or None,
            "service_threshold": service_threshold,
            "service_weight": round(service_weight, 2),
            "service_score": service_score,
            "service_demand_kind": demand_model["service_demand_kind"],
            "market_attractiveness_demand": market_demand or None,
            "market_threshold": market_threshold,
            "market_weight": round(market_weight, 2),
            "market_score": market_score,
            "market_score_cap": round(market_score_cap, 2),
            "market_maximum_credit": market_maximum_credit,
            "market_demand_evidence_type": market_evidence_type,
            "market_demand_kind": demand_model["market_demand_kind"],
            "market_estimator": demand_model["market_estimator"],
            "market_estimation_confidence": demand_model["market_estimation_confidence"],
        }

    def _demand_component_detail(
        self,
        score: float,
        inputs: dict[str, Any],
    ) -> ScoreComponentDetail:
        service_demand = inputs["service_attractiveness_demand"]
        service_text = (
            f"{float(service_demand):g} national monthly searches"
            if isinstance(service_demand, int | float)
            else "no independent national service-demand measurement"
        )
        market_demand = inputs["market_attractiveness_demand"]
        market_text = (
            f"{float(market_demand):g} market-level monthly searches"
            if isinstance(market_demand, int | float)
            else "no market-level demand measurement"
        )
        explanation = (
            f"Service attractiveness uses {service_text}; market attractiveness uses "
            f"{market_text}. "
            f"The evidence is classified as {inputs['market_demand_kind']} "
            f"with {inputs['market_estimation_confidence']} estimation confidence."
        )
        return ScoreComponentDetail(
            score=score,
            maximum_score=self.weights["demand_evidence"],
            formula=(
                "clamp(service_demand / service_threshold * service_weight, 0, "
                "service_weight) + clamp(market_demand / evidence_type_threshold * "
                "market_weight, 0, market_weight * evidence_type_maximum_credit)"
            ),
            inputs=inputs,
            calculation_steps=[
                ScoreCalculationStep(
                    label="Service attractiveness",
                    points=float(inputs["service_score"]),
                    detail=(
                        f"{service_text.capitalize()} against a strong national-demand "
                        f"threshold of {float(inputs['service_threshold']):g}. "
                        "Local volume is not reused for this subcomponent."
                    ),
                    inputs={
                        "demand": service_demand,
                        "threshold": inputs["service_threshold"],
                        "maximum_points": inputs["service_weight"],
                        "demand_kind": inputs["service_demand_kind"],
                    },
                ),
                ScoreCalculationStep(
                    label="Market attractiveness",
                    points=float(inputs["market_score"]),
                    detail=(
                        f"{market_text.capitalize()} against the "
                        f"{str(inputs['market_demand_evidence_type']).replace('_', ' ')} "
                        f"threshold of {float(inputs['market_threshold']):g}. "
                        f"This evidence type can earn at most "
                        f"{float(inputs['market_maximum_credit']):.0%} of the "
                        "market-attractiveness allocation."
                    ),
                    inputs={
                        "demand": market_demand,
                        "threshold": inputs["market_threshold"],
                        "component_point_budget": inputs["market_weight"],
                        "maximum_credit": inputs["market_maximum_credit"],
                        "maximum_points": inputs["market_score_cap"],
                        "evidence_type": inputs["market_demand_evidence_type"],
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
        configured_shares = {
            signal: max(0.0, float(config["signal_shares"][signal]))
            for signal in ("cpc", "paid_competition", "high_intent")
        }
        total_signal_share = sum(configured_shares.values())
        signal_shares = {
            signal: (
                configured_share / total_signal_share
                if total_signal_share > 0
                else 0.0
            )
            for signal, configured_share in configured_shares.items()
        }
        signal_point_budgets = {
            signal: weight * signal_share
            for signal, signal_share in signal_shares.items()
        }
        high_intent_share = high_intent_count / max(1, scored_keyword_count)
        cpc_factor = min(max(avg_cpc / strong_cpc, 0.0), 1.0)
        competition_factor = min(
            max(avg_paid_competition / strong_paid_competition, 0.0),
            1.0,
        )
        intent_factor = min(max(high_intent_share, 0.0), 1.0)
        cpc_points = cpc_factor * signal_point_budgets["cpc"]
        competition_points = (
            competition_factor * signal_point_budgets["paid_competition"]
        )
        intent_points = intent_factor * signal_point_budgets["high_intent"]
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
                    "normalized_factor": round(cpc_factor, 4),
                    "signal_share": round(signal_shares["cpc"], 4),
                    "maximum_points": round(signal_point_budgets["cpc"], 4),
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
                    "normalized_factor": round(competition_factor, 4),
                    "signal_share": round(
                        signal_shares["paid_competition"],
                        4,
                    ),
                    "maximum_points": round(
                        signal_point_budgets["paid_competition"],
                        4,
                    ),
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
                    "normalized_factor": round(intent_factor, 4),
                    "signal_share": round(signal_shares["high_intent"], 4),
                    "maximum_points": round(
                        signal_point_budgets["high_intent"],
                        4,
                    ),
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
                f"clamp((clamp(average_cpc / {strong_cpc:g}, 0, 1) * "
                f"{signal_shares['cpc']:g} + clamp(average_paid_competition / "
                f"{strong_paid_competition:g}, 0, 1) * "
                f"{signal_shares['paid_competition']:g} + "
                f"clamp(high_intent_share, 0, 1) * "
                f"{signal_shares['high_intent']:g}) * {weight:g}, 0, {weight:g})"
            ),
            inputs={
                "average_cpc": round(avg_cpc, 4),
                "strong_cpc": strong_cpc,
                "average_paid_competition": round(avg_paid_competition, 4),
                "strong_paid_competition": strong_paid_competition,
                "high_intent_keyword_count": high_intent_count,
                "scored_keyword_count": scored_keyword_count,
                "high_intent_share": round(high_intent_share, 4),
                "configured_signal_shares": configured_shares,
                "normalized_signal_shares": {
                    signal: round(signal_share, 4)
                    for signal, signal_share in signal_shares.items()
                },
                "signal_point_budgets": {
                    signal: round(point_budget, 4)
                    for signal, point_budget in signal_point_budgets.items()
                },
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
        metrics: list[KeywordMetric],
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["competitor_weakness"]
        supplied_competitor_count = len(competitors)
        competitors = _unique_domain_competitors(competitors)
        duplicate_competitor_count = supplied_competitor_count - len(competitors)
        if not competitors:
            return 0.0, ScoreComponentDetail(
                score=0,
                maximum_score=weight,
                formula=(
                    "exposure_weighted_mean(clamp(link_weakness * "
                    "(1 - relevance_threat_strength * "
                    "weighted_relevance(service, local, service_x_local)) + "
                    "archetype_adjustment, 0, 1)) * "
                    f"{weight:g}"
                ),
                inputs={
                    "competitor_count": 0,
                    "duplicate_competitor_count": duplicate_competitor_count,
                },
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
        observation_queries = list(
            dict.fromkeys(
                observation.query
                for competitor in competitors
                for observation in competitor.serp_observations
            )
        )
        observation_queries.extend(
            competitor.representative_query
            for competitor in competitors
            if not competitor.serp_observations
            and competitor.representative_query
            and competitor.representative_query not in observation_queries
        )
        keyword_evidence = _query_keyword_evidence(
            observation_queries,
            metrics,
            self.config["organic_click"]["keyword_weighting"],
        )
        keyword_evidence_by_query = {
            _normalize_query(str(item["query"])): item
            for item in keyword_evidence
        }
        unmatched_keyword_weight = float(
            self.config["organic_click"]["keyword_weighting"][
                "unmatched_keyword_weight"
            ]
        )
        weak_ref = float(config["weak_referring_domains"])
        strong_ref = float(config["strong_referring_domains"])
        relevance_threat_strength = float(config["relevance_threat_strength"])
        configured_relevance_weights = {
            signal: max(
                0.0,
                float(config["relevance_signal_weights"][signal]),
            )
            for signal in ("service", "local", "interaction")
        }
        total_relevance_weight = sum(configured_relevance_weights.values())
        relevance_signal_weights = {
            signal: (
                configured_weight / total_relevance_weight
                if total_relevance_weight > 0
                else 0.0
            )
            for signal, configured_weight in configured_relevance_weights.items()
        }
        archetype_adjustments = config["archetype_weakness_adjustments"]
        competitor_inputs: list[dict[str, Any]] = []
        archetype_counts: dict[str, int] = {}
        base_total = 0.0
        relevance_deduction_total = 0.0
        archetype_point_totals: dict[str, float] = {}
        unclamped_total = 0.0
        domain_exposure_weights: list[float] = []
        normalized_weakness_values: list[float] = []
        for competitor in competitors:
            ref_domains = competitor.referring_domains
            if ref_domains is None:
                base = float(config["unknown_referring_domains_weakness"])
            else:
                base = 1 - min(max(ref_domains - weak_ref, 0), strong_ref - weak_ref) / max(1, strong_ref - weak_ref)
            service_relevance = (
                competitor.page_relevance_score
                if competitor.page_relevance_score is not None
                else 0.5
            )
            service_relevance = max(0.0, min(1.0, service_relevance))
            local_relevance = max(
                0.0,
                min(1.0, competitor.local_relevance or 0.0),
            )
            relevance_interaction = service_relevance * local_relevance
            direct_relevance = (
                relevance_signal_weights["service"] * service_relevance
                + relevance_signal_weights["local"] * local_relevance
                + relevance_signal_weights["interaction"]
                * relevance_interaction
            )
            direct_threat_penalty = (
                relevance_threat_strength * direct_relevance
            )
            archetype = str(
                competitor.relevance_signals.get("competitor_archetype")
                or competitor.page_type
                or "unknown"
            )
            archetype_adjustment = float(archetype_adjustments.get(archetype, 0))
            relevance_deduction = -(base * direct_threat_penalty)
            raw_value = base + relevance_deduction + archetype_adjustment
            normalized_value = max(0, min(1, raw_value))
            exposures = _competitor_exposures(
                competitor,
                config,
                keyword_evidence_by_query,
                unmatched_keyword_weight,
            )
            domain_exposure_weight = sum(
                float(exposure["exposure_weight"]) for exposure in exposures
            )
            domain_exposure_weights.append(domain_exposure_weight)
            normalized_weakness_values.append(normalized_value)
            best_position_weight = _competitor_position_weight(
                competitor.serp_position,
                config,
            )
            base_total += base * domain_exposure_weight
            relevance_deduction_total += (
                relevance_deduction * domain_exposure_weight
            )
            archetype_point_totals[archetype] = (
                archetype_point_totals.get(archetype, 0)
                + archetype_adjustment * domain_exposure_weight
            )
            archetype_counts[archetype] = archetype_counts.get(archetype, 0) + 1
            unclamped_total += raw_value * domain_exposure_weight
            competitor_inputs.append(
                {
                    "domain": competitor.domain,
                    "representative_query": competitor.representative_query,
                    "serp_position": competitor.serp_position,
                    "serp_position_weight": best_position_weight,
                    "serp_observations": [
                        observation.model_dump(mode="json")
                        for observation in competitor.serp_observations
                    ],
                    "exposure_observations": exposures,
                    "observation_count": len(exposures),
                    "total_exposure_weight": round(
                        domain_exposure_weight,
                        4,
                    ),
                    "referring_domains": ref_domains,
                    "link_weakness": round(base, 4),
                    "service_relevance": round(service_relevance, 4),
                    "local_relevance": round(local_relevance, 4),
                    "relevance_interaction": round(
                        relevance_interaction,
                        4,
                    ),
                    "direct_relevance": round(direct_relevance, 4),
                    "relevance_deduction": round(relevance_deduction, 4),
                    "archetype": archetype,
                    "archetype_adjustment": archetype_adjustment,
                    "unclamped_weakness": round(raw_value, 4),
                    "normalized_weakness": round(normalized_value, 4),
                    "weighted_weakness": round(
                        normalized_value * domain_exposure_weight,
                        4,
                    ),
                    "weighted_threat": round(
                        (1 - normalized_value) * domain_exposure_weight,
                        4,
                    ),
                }
            )
        count = len(competitors)
        total_exposure_weight = sum(domain_exposure_weights)
        weighted_value_total = sum(
            normalized_value * exposure_weight
            for normalized_value, exposure_weight in zip(
                normalized_weakness_values,
                domain_exposure_weights,
                strict=True,
            )
        )
        score = _bounded(
            weighted_value_total / total_exposure_weight * weight,
            weight,
        )
        for item in competitor_inputs:
            item["component_points"] = round(
                item["normalized_weakness"]
                * item["total_exposure_weight"]
                / total_exposure_weight
                * weight,
                4,
            )
            for exposure in item["exposure_observations"]:
                exposure["component_points"] = round(
                    item["normalized_weakness"]
                    * exposure["exposure_weight"]
                    / total_exposure_weight
                    * weight,
                    4,
                )
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
        average_relevance = sum(
            item["direct_relevance"] * item["total_exposure_weight"]
            for item in competitor_inputs
        ) / total_exposure_weight
        average_service_relevance = sum(
            item["service_relevance"] * item["total_exposure_weight"]
            for item in competitor_inputs
        ) / total_exposure_weight
        average_local_relevance = sum(
            item["local_relevance"] * item["total_exposure_weight"]
            for item in competitor_inputs
        ) / total_exposure_weight
        high_relevance_count = sum(
            item["direct_relevance"] >= 0.65 for item in competitor_inputs
        )
        base_points = base_total / total_exposure_weight * weight
        relevance_points = relevance_deduction_total / total_exposure_weight * weight
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
                    "total_exposure_weight": round(total_exposure_weight, 4),
                },
            ),
            ScoreCalculationStep(
                label="Direct relevance threat",
                points=round(relevance_points, 4),
                detail=(
                    f"{high_relevance_count} of {count} pages have direct relevance "
                    f"of at least 0.65; exposure-weighted averages are "
                    f"{average_service_relevance:.2f} service, "
                    f"{average_local_relevance:.2f} local, and "
                    f"{average_relevance:.2f} combined."
                ),
                inputs={
                    "high_relevance_competitor_count": high_relevance_count,
                    "competitor_count": count,
                    "average_direct_relevance": round(average_relevance, 4),
                    "average_service_relevance": round(
                        average_service_relevance,
                        4,
                    ),
                    "average_local_relevance": round(
                        average_local_relevance,
                        4,
                    ),
                    "relevance_threat_strength": relevance_threat_strength,
                    "normalized_relevance_signal_weights": (
                        relevance_signal_weights
                    ),
                    "total_exposure_weight": round(total_exposure_weight, 4),
                },
            ),
        ]
        for archetype in sorted(archetype_point_totals):
            normalized_adjustment = archetype_point_totals[archetype]
            if abs(normalized_adjustment) < 0.0001:
                continue
            points = normalized_adjustment / total_exposure_weight * weight
            per_result = float(archetype_adjustments.get(archetype, 0))
            steps.append(
                ScoreCalculationStep(
                    label=f"{archetype.replace('_', ' ').title()} adjustment",
                    points=round(points, 4),
                    detail=(
                        f"{archetype_counts[archetype]} unique "
                        f"{archetype.replace('_', ' ')} domain(s) received an "
                        f"exposure-weighted {per_result:+.2f} weakness adjustment."
                    ),
                    inputs={
                        "archetype": archetype,
                        "result_count": archetype_counts[archetype],
                        "adjustment_per_result": per_result,
                    },
                )
            )
        clamp_points = (
            (weighted_value_total - unclamped_total)
            / total_exposure_weight
            * weight
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
                "query_and_position_exposure_weighted_mean(clamp(link_weakness * (1 - "
                f"{relevance_threat_strength:g} * ("
                f"{relevance_signal_weights['service']:g} * service_relevance + "
                f"{relevance_signal_weights['local']:g} * local_relevance + "
                f"{relevance_signal_weights['interaction']:g} * "
                "service_relevance * local_relevance)) + "
                f"archetype_adjustment, 0, 1)) * {weight:g}"
            ),
            inputs={
                "competitor_count": count,
                "supplied_competitor_count": supplied_competitor_count,
                "duplicate_competitor_count": duplicate_competitor_count,
                "observation_count": sum(
                    int(item["observation_count"]) for item in competitor_inputs
                ),
                "total_exposure_weight": round(total_exposure_weight, 4),
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
                "configured_relevance_signal_weights": (
                    configured_relevance_weights
                ),
                "normalized_relevance_signal_weights": (
                    relevance_signal_weights
                ),
                "serp_position_weights": config["serp_position_weights"],
                "keyword_weighting": self.config["organic_click"][
                    "keyword_weighting"
                ],
                "keyword_evidence": keyword_evidence,
                "unpositioned_weight": float(config["unpositioned_weight"]),
                "archetype_weakness_adjustments": archetype_adjustments,
                "per_competitor": competitor_inputs,
            },
            calculation_steps=steps,
            explanation=(
                f"Median referring domains are {median_text}; {high_relevance_count} of "
                f"{count} unique-domain competitors are strongly relevant. Results were "
                "weighted across every observed query and organic position using "
                f"representative keyword value; observed archetypes: "
                f"{archetype_summary}."
            ),
        )

    def _organic_click_availability(
        self,
        serp_snapshots: list[SerpSnapshot],
        metrics: list[KeywordMetric],
    ) -> tuple[float, ScoreComponentDetail]:
        weight = self.weights["organic_click_availability"]
        results = [
            result
            for snapshot in serp_snapshots
            for result in snapshot.results
            if result.result_type == "organic"
        ]
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
        position_weights = {
            int(position): float(position_weight)
            for position, position_weight in penalties["serp_position_weights"].items()
        }
        position_capacity = sum(position_weights.values())
        serp_evidence = _serp_keyword_evidence(
            serp_snapshots,
            metrics,
            penalties["keyword_weighting"],
        )
        total_serp_weight = sum(
            float(item["keyword_weight"]) for item in serp_evidence
        )
        total_result_capacity = position_capacity * total_serp_weight
        classification_penalties = {
            "directory": float(penalties["directory_penalty"]),
            "marketplace": float(penalties["marketplace_penalty"]),
            "national_brand": float(penalties["national_brand_penalty"]),
            "lead_generator": float(penalties["lead_generator_penalty"]),
            "informational_publisher": float(penalties["informational_publisher_penalty"]),
            "unknown": float(penalties["unknown_penalty"]),
        }
        shopping_types = {
            str(result_type).lower()
            for result_type in penalties.get("shopping_product_result_types", [])
        }
        shopping_serp_share = _weighted_serp_feature_share(
            serp_snapshots,
            serp_evidence,
            lambda snapshot: bool(
                shopping_types
                & {
                    *(feature.lower() for feature in snapshot.features_present),
                    *(result.result_type.lower() for result in snapshot.results),
                }
            ),
        )
        local_pack_serp_share = _weighted_serp_feature_share(
            serp_snapshots,
            serp_evidence,
            lambda snapshot: "local_pack" in snapshot.features_present,
        )
        ads_top_serp_share = _weighted_serp_feature_share(
            serp_snapshots,
            serp_evidence,
            lambda snapshot: "ads_top" in snapshot.features_present,
        )
        availability = 1.0
        classification_counts = _serp_composition(results)
        classification_weight_totals: dict[str, float] = {}
        result_evidence: list[dict[str, Any]] = []
        for snapshot, snapshot_evidence in zip(
            serp_snapshots,
            serp_evidence,
            strict=True,
        ):
            keyword_weight = float(snapshot_evidence["keyword_weight"])
            for result in snapshot.results:
                if result.result_type != "organic":
                    continue
                position_weight = position_weights.get(result.order, 0.0)
                evidence_weight = keyword_weight * position_weight
                classification_weight_totals[result.classification] = (
                    classification_weight_totals.get(result.classification, 0.0)
                    + evidence_weight
                )
                result_evidence.append(
                    {
                        "query": snapshot.query,
                        "domain": result.domain,
                        "position": result.order,
                        "classification": result.classification,
                        "position_weight": position_weight,
                        "keyword_weight": round(keyword_weight, 4),
                        "evidence_weight": round(evidence_weight, 4),
                    }
                )
        classification_weighted_shares = {
            classification: round(
                classification_weight / total_result_capacity,
                4,
            )
            for classification, classification_weight in classification_weight_totals.items()
        }
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
            result_share = (
                classification_weight_totals.get(classification, 0.0)
                / total_result_capacity
            )
            deduction = result_share * penalty
            availability -= deduction
            if result_count:
                steps.append(
                    ScoreCalculationStep(
                        label=f"{classification.replace('_', ' ').title()} displacement",
                        points=round(-(deduction * weight), 4),
                        detail=(
                            f"{result_count} {classification.replace('_', ' ')} "
                            "result(s), weighted by organic position and representative "
                            f"keyword value, occupy {result_share:.1%} of top-10 capacity."
                        ),
                        inputs={
                            "result_count": result_count,
                            "weighted_result_share": round(result_share, 4),
                            "penalty_rate": penalty,
                        },
                    )
                )
        shopping_penalty = float(penalties["shopping_product_penalty"])
        shopping_deduction = shopping_serp_share * shopping_penalty
        availability -= shopping_deduction
        if shopping_serp_share:
            steps.append(
                ScoreCalculationStep(
                    label="Shopping/product displacement",
                    points=round(-(shopping_deduction * weight), 4),
                    detail=(
                        f"Shopping or product features appear in "
                        f"{shopping_serp_share:.0%} of the keyword-weighted SERP sample."
                    ),
                    inputs={
                        "weighted_serp_share": round(shopping_serp_share, 4),
                        "penalty_rate": shopping_penalty,
                    },
                )
            )
        if local_pack_serp_share:
            local_pack_deduction = (
                local_pack_serp_share * float(penalties["local_pack_penalty"])
            )
            availability -= local_pack_deduction
            steps.append(
                ScoreCalculationStep(
                    label="Local-pack displacement",
                    points=round(-(local_pack_deduction * weight), 4),
                    detail=(
                        f"Local packs affect {local_pack_serp_share:.1%} of the "
                        "keyword-weighted SERP sample."
                    ),
                    inputs={
                        "weighted_serp_share": round(local_pack_serp_share, 4),
                        "penalty_rate": float(penalties["local_pack_penalty"]),
                    },
                )
            )
        if ads_top_serp_share:
            ads_deduction = ads_top_serp_share * float(penalties["ads_top_penalty"])
            availability -= ads_deduction
            steps.append(
                ScoreCalculationStep(
                    label="Top-ad displacement",
                    points=round(-(ads_deduction * weight), 4),
                    detail=(
                        f"Top ads affect {ads_top_serp_share:.1%} of the "
                        "keyword-weighted SERP sample."
                    ),
                    inputs={
                        "weighted_serp_share": round(ads_top_serp_share, 4),
                        "penalty_rate": float(penalties["ads_top_penalty"]),
                    },
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
        if local_pack_serp_share:
            active_features.append(
                f"local packs on {local_pack_serp_share:.0%} of weighted SERPs"
            )
        if ads_top_serp_share:
            active_features.append(
                f"top ads on {ads_top_serp_share:.0%} of weighted SERPs"
            )
        if shopping_serp_share:
            active_features.append(
                f"shopping/product features on {shopping_serp_share:.0%} of weighted SERPs"
            )
        feature_text = ", ".join(active_features) if active_features else "none"
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                f"clamp((1 - sum(position_and_keyword_weighted_result_share * "
                f"classification_penalty) - weighted_shopping_serp_share * "
                f"{shopping_penalty:g} - weighted_local_pack_serp_share * "
                f"{float(penalties['local_pack_penalty']):g} - "
                f"weighted_ads_top_serp_share * "
                f"{float(penalties['ads_top_penalty']):g}) * "
                f"{weight:g}, 0, {weight:g})"
            ),
            inputs={
                "serp_snapshot_count": len(serp_snapshots),
                "serp_result_count": len(results),
                "classification_counts": classification_counts,
                "classification_weighted_shares": classification_weighted_shares,
                "classification_penalties": classification_penalties,
                "serp_position_weights": position_weights,
                "position_capacity_per_serp": round(position_capacity, 4),
                "total_result_capacity": round(total_result_capacity, 4),
                "serp_keyword_evidence": serp_evidence,
                "per_result_evidence": result_evidence,
                "shopping_serp_share": round(shopping_serp_share, 4),
                "shopping_product_penalty": shopping_penalty,
                "local_pack_serp_share": round(local_pack_serp_share, 4),
                "local_pack_penalty": float(penalties["local_pack_penalty"]),
                "ads_top_serp_share": round(ads_top_serp_share, 4),
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
                    f"clamp((supply_fit * 0.65 + suitable_quality * 0.35) * "
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
        quality_score = cast(
            float,
            summary["average_top_suitable_provider_score"],
        )
        median_suitable_score = cast(
            float | None,
            summary["median_suitable_provider_score"],
        )
        suitable_share = cast(float, summary["suitable_provider_share"])
        suitable_threshold = cast(float, summary["suitable_threshold"])
        ideal_min = int(self.config["providers"]["ideal_min"])
        ideal_max = int(self.config["providers"]["ideal_max"])
        oversupply = int(self.config["providers"]["oversupply_count"])
        base_supply_fit = min(suitable / max(1, ideal_min), 1.0)
        supply_multiplier = 1.0
        if suitable > oversupply:
            supply_multiplier = 0.72
        elif suitable > ideal_max:
            supply_multiplier = 0.88
        supply_fit = base_supply_fit * supply_multiplier
        quality = quality_score / 100
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
                f"{suitable} suitable providers exceed the {oversupply}-provider "
                "oversupply threshold."
                if suitable > oversupply
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
                        "supply_count_basis": "suitable_provider_count",
                        "saturation_supply_count": suitable,
                        "ideal_max": ideal_max,
                        "oversupply_count": oversupply,
                    },
                )
            )
        steps.append(
            ScoreCalculationStep(
                label="Top suitable provider quality",
                points=round(quality_points, 4),
                detail=(
                    (
                        f"The top suitable-provider average is {quality_score:.2f}; "
                        f"the suitable-provider median is {median_suitable_score:.2f}, "
                        f"and {suitable_share:.1%} of raw listings are suitable."
                    )
                    if median_suitable_score is not None
                    else (
                        "No providers meet the suitability threshold, so tenant "
                        "quality receives no points."
                    )
                ),
                inputs={
                    "average_top_suitable_provider_score": quality_score,
                    "median_suitable_provider_score": median_suitable_score,
                    "suitable_provider_share": suitable_share,
                    "raw_average_suitability_score": summary[
                        "raw_average_suitability_score"
                    ],
                    "quality_factor": round(quality, 4),
                },
            )
        )
        return score, ScoreComponentDetail(
            score=score,
            maximum_score=weight,
            formula=(
                f"clamp((adjusted_supply_fit * 0.65 + "
                f"(average_top_suitable_provider_score / 100) * 0.35) * "
                f"{weight:g}, 0, {weight:g})"
            ),
            inputs={
                **summary,
                "ideal_min": ideal_min,
                "ideal_max": ideal_max,
                "oversupply_count": oversupply,
                "supply_count_basis": "suitable_provider_count",
                "saturation_supply_count": suitable,
                "supply_fit_before_adjustment": round(base_supply_fit, 4),
                "supply_multiplier": supply_multiplier,
                "adjusted_supply_fit": round(supply_fit, 4),
            },
            calculation_steps=steps,
            explanation=(
                f"{suitable} of {len(providers)} providers meet the configured "
                f"suitability threshold. Their top-sample average is "
                f"{quality_score:.2f}, their median is "
                f"{median_suitable_score:.2f}. "
                f"The supply multiplier is {supply_multiplier:.2f}."
                if median_suitable_score is not None
                else (
                    f"None of {len(providers)} providers meet the configured "
                    "suitability threshold."
                )
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
        configured = self.config["missing_data_penalties"]
        raw_penalties = {
            field: max(0.0, float(configured.get(field, 0)))
            for field in missing
        }
        raw_total = sum(raw_penalties.values())
        scale = (
            max_penalty / raw_total
            if raw_total > max_penalty and raw_total > 0
            else 1.0
        )
        return {
            field: round(penalty * scale, 2)
            for field, penalty in raw_penalties.items()
            if penalty > 0
        }

    def _evidence_status(
        self,
        missing: list[str],
        assessment_type: str,
    ) -> str:
        if assessment_type == "preliminary":
            return "preliminary"
        if "competitor_metrics" in missing:
            return "unusable"
        if missing:
            return "partial"
        return "complete"

    def _missing_evidence_score_cap(
        self,
        missing: list[str],
        assessment_type: str,
    ) -> float | None:
        if assessment_type == "preliminary":
            return None
        configured_caps = self.config["missing_evidence_score_caps"]
        active_caps = [
            float(configured_caps[field])
            for field in missing
            if field in configured_caps
        ]
        return min(active_caps) if active_caps else None

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
        unknown_serp_share: float,
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

        maximum_unknown_share = float(
            config["maximum_unknown_serp_share_for_high"]
        )
        if unknown_serp_share > maximum_unknown_share:
            deduct(
                "unknown_serp_classification",
                float(deductions_config["unknown_serp_classification"])
                * unknown_serp_share,
                (
                    f"Unknown results occupy {unknown_serp_share:.1%} of "
                    "position-and-keyword-weighted organic capacity."
                ),
            )
            cap(
                Confidence.medium,
                "Insufficient SERP classification coverage prevents high confidence.",
            )

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

        if "competitor_metrics" in missing and assessment_type != "preliminary":
            cap(
                Confidence.insufficient,
                "A full assessment without competitor evidence is unusable.",
            )
        if (
            {"serp_snapshots", "serp_results"} & set(missing)
            and assessment_type != "preliminary"
        ):
            cap(
                Confidence.low,
                "Missing SERP evidence makes the full assessment partial.",
            )
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
            "weighted_unknown_serp_share": round(unknown_serp_share, 4),
            "classification_coverage": round(1 - unknown_serp_share, 4),
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
                "Provider-local keyword volume supports market attractiveness only; no "
                "independent national volume was available for service attractiveness."
            )
        return assumptions


def _market_demand_evidence_type(demand_model: dict[str, Any]) -> str:
    if demand_model["market_demand_kind"] == "measured_local":
        return "measured_local"
    if demand_model["market_demand_kind"] == "estimated_local":
        if demand_model["market_estimator"] == "population_share":
            return "population_estimated"
        return "estimated_local"
    return "missing"


def _unique_domain_competitors(
    competitors: list[CompetitorMetric],
) -> list[CompetitorMetric]:
    by_domain: dict[str, CompetitorMetric] = {}
    for competitor in competitors:
        domain = (
            competitor.domain
            or urlparse(competitor.url).netloc
            or competitor.url
        ).lower().removeprefix("www.").strip()
        existing = by_domain.get(domain)
        if existing is None:
            by_domain[domain] = competitor
            continue

        chosen = min(
            (existing, competitor),
            key=lambda item: (
                item.serp_position is None,
                item.serp_position or 0,
            ),
        )
        observations = {
            (item.query, item.position, item.url): item
            for item in [*existing.serp_observations, *competitor.serp_observations]
        }
        ordered_observations = sorted(
            observations.values(),
            key=lambda item: (item.position, item.query, item.url),
        )
        update: dict[str, Any] = {"serp_observations": ordered_observations}
        if ordered_observations:
            update["representative_query"] = ordered_observations[0].query
            update["serp_position"] = ordered_observations[0].position
        by_domain[domain] = chosen.model_copy(update=update)
    return list(by_domain.values())


def _competitor_position_weight(
    position: int | None,
    config: dict[str, Any],
) -> float:
    if position is None:
        return float(config["unpositioned_weight"])
    configured = cast(dict[Any, Any], config["serp_position_weights"])
    exact = configured.get(position, configured.get(str(position)))
    if exact is not None:
        return float(exact)
    return min(float(value) for value in configured.values())


def _competitor_exposures(
    competitor: CompetitorMetric,
    config: dict[str, Any],
    keyword_evidence_by_query: dict[str, dict[str, Any]],
    unmatched_keyword_weight: float,
) -> list[dict[str, Any]]:
    observations: list[tuple[str | None, int | None, str]] = [
        (observation.query, observation.position, observation.url)
        for observation in competitor.serp_observations
    ]
    if not observations:
        observations = [
            (
                competitor.representative_query,
                competitor.serp_position,
                competitor.url,
            )
        ]

    exposures: list[dict[str, Any]] = []
    for query, position, url in observations:
        keyword_evidence = (
            keyword_evidence_by_query.get(_normalize_query(query))
            if query
            else None
        )
        keyword_weight = max(
            0.0001,
            float(
                keyword_evidence["keyword_weight"]
                if keyword_evidence is not None
                else unmatched_keyword_weight
            ),
        )
        position_weight = _competitor_position_weight(position, config)
        exposures.append(
            {
                "query": query,
                "position": position,
                "url": url,
                "matched_keyword": (
                    keyword_evidence["matched_keyword"]
                    if keyword_evidence is not None
                    else None
                ),
                "keyword_weight": round(keyword_weight, 4),
                "keyword_weight_basis": (
                    keyword_evidence["weight_basis"]
                    if keyword_evidence is not None
                    else "unmatched_keyword"
                ),
                "position_weight": round(position_weight, 4),
                "exposure_weight": round(
                    keyword_weight * position_weight,
                    4,
                ),
            }
        )
    return exposures


def _serp_keyword_evidence(
    serp_snapshots: list[SerpSnapshot],
    metrics: list[KeywordMetric],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    return _query_keyword_evidence(
        [snapshot.query for snapshot in serp_snapshots],
        metrics,
        config,
    )


def _query_keyword_evidence(
    queries: list[str],
    metrics: list[KeywordMetric],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    metrics_by_query: dict[str, KeywordMetric] = {}
    for metric in metrics:
        for value in (metric.keyword, metric.canonical_keyword):
            metrics_by_query.setdefault(_normalize_query(value), metric)
    matched_metrics = [
        metrics_by_query.get(_normalize_query(query))
        for query in queries
    ]
    max_volume = max(
        (
            metric.search_volume
            for metric in matched_metrics
            if metric is not None and metric.search_volume is not None
        ),
        default=0,
    )
    max_cpc = max(
        (
            metric.cpc
            for metric in matched_metrics
            if metric is not None and metric.cpc is not None
        ),
        default=0.0,
    )
    demand_share = float(config["demand_share"])
    commercial_share = float(config["commercial_share"])
    minimum_weight = float(config["minimum_weight"])
    unmatched_weight = float(config["unmatched_keyword_weight"])
    evidence: list[dict[str, Any]] = []
    for query, matched_metric in zip(
        queries,
        matched_metrics,
        strict=True,
    ):
        if matched_metric is None:
            evidence.append(
                {
                    "query": query,
                    "matched_keyword": None,
                    "search_volume": None,
                    "cpc": None,
                    "demand_signal": None,
                    "commercial_signal": None,
                    "keyword_weight": unmatched_weight,
                    "weight_basis": "unmatched_keyword",
                }
            )
            continue

        weighted_signals: list[tuple[float, float]] = []
        demand_signal: float | None = None
        commercial_signal: float | None = None
        if matched_metric.search_volume is not None and max_volume > 0:
            demand_signal = matched_metric.search_volume / max_volume
            weighted_signals.append((demand_share, demand_signal))
        if matched_metric.cpc is not None and max_cpc > 0:
            commercial_signal = matched_metric.cpc / max_cpc
            weighted_signals.append((commercial_share, commercial_signal))
        if weighted_signals:
            signal_weight = sum(signal_share for signal_share, _ in weighted_signals)
            raw_weight = (
                sum(
                    signal_share * signal
                    for signal_share, signal in weighted_signals
                )
                / signal_weight
            )
            keyword_weight = max(minimum_weight, raw_weight)
            weight_basis = "relative_demand_and_commercial_value"
        else:
            keyword_weight = unmatched_weight
            weight_basis = "matched_without_weighting_metrics"
        evidence.append(
            {
                "query": query,
                "matched_keyword": matched_metric.keyword,
                "search_volume": matched_metric.search_volume,
                "cpc": matched_metric.cpc,
                "demand_signal": (
                    round(demand_signal, 4)
                    if demand_signal is not None
                    else None
                ),
                "commercial_signal": (
                    round(commercial_signal, 4)
                    if commercial_signal is not None
                    else None
                ),
                "keyword_weight": round(keyword_weight, 4),
                "weight_basis": weight_basis,
            }
        )
    return evidence


def _weighted_serp_feature_share(
    serp_snapshots: list[SerpSnapshot],
    serp_evidence: list[dict[str, Any]],
    predicate: Callable[[SerpSnapshot], bool],
) -> float:
    total_weight = sum(float(item["keyword_weight"]) for item in serp_evidence)
    if total_weight <= 0:
        return 0.0
    affected_weight = sum(
        float(item["keyword_weight"])
        for snapshot, item in zip(
            serp_snapshots,
            serp_evidence,
            strict=True,
        )
        if predicate(snapshot)
    )
    return affected_weight / total_weight


def _normalize_query(value: str) -> str:
    return " ".join(value.casefold().split())


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
