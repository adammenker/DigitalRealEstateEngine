from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import (
    ApiCallORM,
    FullOpportunityScoreORM,
    PreliminaryAssessmentORM,
    ScanRunORM,
    ScoreComponentORM,
)
from rank_rent.domain.interfaces import DomainAvailabilityProvider, MarketResearchProvider
from rank_rent.domain.models import Market, OpportunityScore, ServiceFamily
from rank_rent.integrations.factory import (
    build_domain_availability_provider,
    build_market_research_provider,
)
from rank_rent.planning import build_scan_plan
from rank_rent.replay import DatabaseReplayTransport
from rank_rent.repositories import (
    get_or_create_opportunity,
    save_artifact,
    upsert_market,
    upsert_service,
)
from rank_rent.runtime import DataMode, resolve_data_mode
from rank_rent.scoring.score import OpportunityScorer
from rank_rent.scoring.serp import classify_result
from rank_rent.services.competitors import enrich_competitors
from rank_rent.services.discovery_report import build_discovery_report
from rank_rent.services.domains import generate_domain_candidates
from rank_rent.services.keywords import (
    plan_keyword_candidates,
    rank_and_cluster_keyword_metrics,
    service_keyword_terms,
)
from rank_rent.services.outreach import generate_initial_email
from rank_rent.services.providers import score_provider_suitability
from rank_rent.services.records import save_scan_plan_calls, save_scan_records
from rank_rent.settings import get_settings
from rank_rent.site_generator.generator import build_site_config, generate_static_site


class ScanCancelled(Exception):
    """Raised when a persisted scan has been cancelled before the next stage."""


class ScanPipeline:
    def __init__(
        self,
        session: Session,
        research_provider: MarketResearchProvider | None = None,
        domain_provider: DomainAvailabilityProvider | None = None,
        data_mode: DataMode | str | None = None,
    ) -> None:
        self.session = session
        self.settings = get_settings()
        self.data_mode = resolve_data_mode(data_mode or self.settings.data_mode)
        replay_transport = (
            DatabaseReplayTransport(session)
            if self.data_mode == DataMode.replay and research_provider is None
            else None
        )
        self.research_provider = research_provider or build_market_research_provider(
            self.settings,
            self.data_mode,
            replay_transport=replay_transport,
            session=session,
        )
        self.domain_provider = domain_provider or build_domain_availability_provider(
            self.settings,
            self.data_mode,
        )
        self.live_scan_depth = self.settings.live_scan_depth.lower().strip()
        self.scorer = OpportunityScorer()

    async def run(
        self,
        service: ServiceFamily,
        market: Market,
        *,
        source: str = "manual",
        build_site: bool = False,
        existing_scan_id: int | None = None,
    ) -> dict[str, Any]:
        plan = build_scan_plan(self.settings, self.data_mode, service, market, session=self.session)
        if plan.blocked:
            raise RuntimeError(plan.block_reason or "Scan blocked by cost policy.")
        service_row = upsert_service(self.session, service)
        market_row = upsert_market(self.session, market)
        opportunity = get_or_create_opportunity(self.session, service_row, market_row)
        scan = self._prepare_scan_run(
            existing_scan_id=existing_scan_id,
            opportunity_id=opportunity.id,
            source=source,
            service=service,
            market=market,
            plan=plan,
        )
        self.session.flush()
        if hasattr(self.research_provider, "current_scan_run_id"):
            self.research_provider.current_scan_run_id = scan.id
        save_scan_plan_calls(self.session, scan.id, plan)

        try:
            self._ensure_not_cancelled(scan)
            if (
                self.data_mode == DataMode.live
                and not market.provider_location_code
                and not market.provider_location_name
            ):
                self._ensure_not_cancelled(scan)
                self._set_stage(scan, "resolving_location")
                market = (await self.research_provider.resolve_location(market.display_name)).market
                market_row = upsert_market(self.session, market)
                opportunity = get_or_create_opportunity(self.session, service_row, market_row)
                scan.opportunity_id = opportunity.id
                scan.request_parameters = {
                    **scan.request_parameters,
                    "market": market.slug,
                    "resolved_market": {
                        "display_name": market.display_name,
                        "provider_location_code": market.provider_location_code,
                        "provider_location_name": market.provider_location_name,
                        "granularity": market.type.value,
                    },
                }
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "discovering_keywords")
            candidates = await self.research_provider.discover_keywords(service, market)
            _, negative_terms = service_keyword_terms(service)
            keyword_candidates = plan_keyword_candidates(candidates, negative_terms)
            included_keywords = keyword_candidates.included_keywords
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "fetching_metrics")
            raw_metrics = await self.research_provider.get_keyword_metrics(included_keywords, market)
            keyword_metrics = rank_and_cluster_keyword_metrics(
                raw_metrics,
                service=service,
                market=market,
                selected_limit=self.serp_keyword_limit,
                existing_decisions=keyword_candidates.decisions,
            )
            metrics = keyword_metrics.metrics
            representative = keyword_metrics.selected_serp_keywords
            serp_snapshots = []
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "fetching_serps")
            for keyword in representative:
                self._ensure_not_cancelled(scan)
                snapshot = await self.research_provider.get_serp_snapshot(keyword, market)
                snapshot.results = [classify_result(result) for result in snapshot.results]
                serp_snapshots.append(snapshot)
            competitor_urls = [
                r.url for s in serp_snapshots for r in s.results if r.result_type == "organic"
            ][: self.backlink_competitor_limit]
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "fetching_competitors")
            raw_competitors = (
                await self.research_provider.get_competitor_metrics(competitor_urls)
                if competitor_urls
                else []
            )
            competitors = enrich_competitors(raw_competitors, serp_snapshots, service, market)
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "fetching_providers")
            providers = score_provider_suitability(
                await self.research_provider.find_providers(service, market),
                service,
                market,
            )
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "scoring")
            score = self.scorer.score(
                keyword_metrics.scoring_metrics,
                serp_snapshots,
                competitors,
                providers,
                market,
            )
            is_preliminary = self.is_preliminary_assessment
            save_scan_records(
                self.session,
                scan_run_id=scan.id,
                opportunity_id=opportunity.id,
                metrics=metrics,
                serp_snapshots=serp_snapshots,
                competitors=competitors,
                providers=providers,
                keyword_clusters=keyword_metrics.clusters,
                keyword_decisions=keyword_metrics.decisions,
            )
            domains = (
                await generate_domain_candidates(service, market, self.domain_provider)
                if build_site
                else []
            )
            outreach = (
                [
                    generate_initial_email(provider, service, market).model_dump(mode="json")
                    for provider in providers[:2]
                ]
                if build_site
                else []
            )
            site_config = build_site_config(service, market, domains[0].domain if domains else None) if build_site else None
            site_path: Path | None = generate_static_site(site_config) if build_site and site_config else None

            scan_metadata = {
                "scan_run_id": scan.id,
                "data_mode": self.data_mode.value,
                "scan_profile": scan.scan_profile,
                "planned_cost_usd": scan.planned_cost_usd,
                "estimated_paid_api_calls": self.estimated_paid_api_calls,
                "started_at": scan.started_at.isoformat() if scan.started_at else None,
                "adapter_names": scan.adapter_names,
                "adapter_versions": scan.adapter_versions,
                "normalization_version": scan.normalization_version,
                "cache_policy_version": scan.cache_policy_version,
            }
            discovery_report = build_discovery_report(
                service=service,
                market=market,
                metrics=keyword_metrics.scoring_metrics,
                serp_snapshots=serp_snapshots,
                competitors=competitors,
                providers=providers,
                score=score,
                scan_metadata=scan_metadata,
            )

            save_artifact(
                self.session,
                opportunity.id,
                "preliminary_assessment" if is_preliminary else "scan_result",
                {
                    "data_mode": self.data_mode.value,
                    "live_scan_depth": self.live_scan_depth if self.data_mode == DataMode.live else None,
                    "estimated_paid_api_calls": self.estimated_paid_api_calls,
                    "assessment_type": "preliminary" if is_preliminary else "full",
                    "unavailable_components": self.unavailable_components,
                    "additional_calls_required_for_full_scan": self.additional_calls_required_for_full_scan,
                    "demand_evidence": self._demand_evidence(metrics, market),
                    "scan_plan": plan.model_dump(mode="json"),
                    "keywords": [k.model_dump(mode="json") for k in keyword_candidates.candidates],
                    "keyword_clusters": [
                        cluster.__dict__ for cluster in keyword_metrics.clusters
                    ],
                    "keyword_decisions": [
                        decision.__dict__ for decision in keyword_metrics.decisions
                    ],
                    "representative_keywords": representative,
                    "metrics": [m.model_dump(mode="json") for m in metrics],
                    "serp_snapshots": [s.model_dump(mode="json") for s in serp_snapshots],
                    "competitors": [c.model_dump(mode="json") for c in competitors],
                    "providers": [p.model_dump(mode="json") for p in providers],
                    "score": score.model_dump(mode="json"),
                    "discovery_report": discovery_report,
                },
            )
            save_artifact(self.session, opportunity.id, "discovery_report", discovery_report)
            self._save_assessment_records(scan, opportunity.id, score, is_preliminary)
            if build_site and site_config:
                save_artifact(
                    self.session,
                    opportunity.id,
                    "domain_candidates",
                    {"domains": [d.model_dump(mode="json") for d in domains]},
                )
                save_artifact(self.session, opportunity.id, "outreach_drafts", {"drafts": outreach})
                save_artifact(
                    self.session,
                    opportunity.id,
                    "site_config",
                    {
                        "config": site_config.model_dump(mode="json"),
                        "generated_path": str(site_path) if site_path else None,
                    },
                )
            opportunity.status = "preliminary_review" if is_preliminary else "full_review"
            if is_preliminary:
                if opportunity.latest_score is None:
                    opportunity.confidence = "preliminary"
                    opportunity.score_version = score.scoring_version
            else:
                opportunity.latest_score = score.total_score
                opportunity.score_version = score.scoring_version
                opportunity.confidence = score.confidence.value
            opportunity.missing_data_flags = score.missing_fields
            scan.status = "completed"
            scan.progress_stage = "completed"
            scan.completed_at = datetime.now(UTC)
            scan.actual_cost_usd = self._actual_api_cost_for_scan(scan.id)
            scan.scoring_version = score.scoring_version
            self.session.commit()
            return {
                "opportunity_id": opportunity.id,
                "scan_id": scan.id,
                "data_mode": self.data_mode.value,
                "score": score,
                "assessment_type": "preliminary" if is_preliminary else "full",
                "scan_plan": plan,
                "domains": domains,
                "providers": providers,
                "site_path": site_path,
            }
        except ScanCancelled:
            scan.status = "cancelled"
            scan.progress_stage = "cancelled"
            scan.completed_at = datetime.now(UTC)
            scan.actual_cost_usd = self._actual_api_cost_for_scan(scan.id)
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "cancelled": True,
            }
            self.session.commit()
            raise
        except Exception as exc:
            failed_stage = scan.progress_stage
            scan.status = "failed"
            scan.progress_stage = "failed"
            scan.error_summary = str(exc)
            scan.completed_at = datetime.now(UTC)
            scan.actual_cost_usd = self._actual_api_cost_for_scan(scan.id)
            opportunity.status = "scan_failed"
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "failed_stage": failed_stage,
                "error": str(exc),
            }
            self.session.commit()
            raise

    def _prepare_scan_run(
        self,
        *,
        existing_scan_id: int | None,
        opportunity_id: int,
        source: str,
        service: ServiceFamily,
        market: Market,
        plan: Any,
    ) -> ScanRunORM:
        scan = self.session.get(ScanRunORM, existing_scan_id) if existing_scan_id else None
        if scan is None:
            scan = ScanRunORM(opportunity_id=opportunity_id, source=source)
            self.session.add(scan)
        scan.opportunity_id = opportunity_id
        scan.source = source
        scan.status = "running"
        scan.data_mode = self.data_mode.value
        scan.scan_profile = self.live_scan_depth if self.data_mode == DataMode.live else "full"
        scan.adapter_names = {
            "market_research": getattr(
                self.research_provider,
                "provider_name",
                type(self.research_provider).__name__,
            ),
            "domain": type(self.domain_provider).__name__,
        }
        scan.adapter_versions = {
            "market_research_api": getattr(self.research_provider, "api_version", "fixture"),
            "domain": "v1",
        }
        scan.normalization_version = "v1"
        scan.cache_policy_version = "v2"
        scan.planned_cost_usd = float(plan.estimated_uncached_cost_usd)
        scan.progress_stage = "planning"
        scan.estimated_cost_usd = float(plan.estimated_uncached_cost_usd)
        scan.actual_cost_usd = 0
        scan.started_at = datetime.now(UTC)
        scan.completed_at = None
        scan.error_summary = None
        scan.integration_versions = {
            "data_mode": self.data_mode.value,
            "market_research_provider": getattr(
                self.research_provider,
                "provider_name",
                type(self.research_provider).__name__,
            ),
            "domain_provider": type(self.domain_provider).__name__,
            "live_scan_depth": self.live_scan_depth if self.data_mode == DataMode.live else None,
            "cache_policy_version": "v2",
        }
        scan.request_parameters = {
            "service": service.slug,
            "market": market.slug,
            "data_mode": self.data_mode.value,
            "live_scan_depth": self.live_scan_depth if self.data_mode == DataMode.live else None,
            "scan_plan": plan.model_dump(mode="json"),
            "service_payload": service.model_dump(mode="json"),
            "market_payload": market.model_dump(mode="json"),
        }
        return scan

    def _ensure_not_cancelled(self, scan: ScanRunORM) -> None:
        self.session.refresh(scan)
        if scan.cancel_requested:
            raise ScanCancelled(f"Scan {scan.id} was cancelled.")

    def _set_stage(self, scan: ScanRunORM, stage: str) -> None:
        scan.progress_stage = stage
        scan.partial_outputs = {
            **(scan.partial_outputs or {}),
            "last_successful_stage": stage,
        }
        self.session.commit()

    def _save_assessment_records(
        self,
        scan: ScanRunORM,
        opportunity_id: int,
        score: OpportunityScore,
        is_preliminary: bool,
    ) -> None:
        payload = score.model_dump(mode="json")
        if is_preliminary:
            self.session.add(
                PreliminaryAssessmentORM(
                    scan_run_id=scan.id,
                    opportunity_id=opportunity_id,
                    scoring_version=score.scoring_version,
                    confidence="preliminary",
                    missing_components=self.unavailable_components + score.missing_fields,
                    payload=payload,
                )
            )
        else:
            self.session.add(
                FullOpportunityScoreORM(
                    scan_run_id=scan.id,
                    opportunity_id=opportunity_id,
                    scoring_version=score.scoring_version,
                    total_score=score.total_score,
                    confidence=score.confidence.value,
                    explanation=score.explanation,
                    payload=payload,
                )
            )
        for component, value in score.component_scores.items():
            self.session.add(
                ScoreComponentORM(
                    scan_run_id=scan.id,
                    component=component,
                    score=value,
                    inputs=score.input_measurements,
                    formula=f"{score.scoring_version}:{component}",
                    penalties=score.missing_data_penalties,
                )
            )

    def _demand_evidence(self, metrics: list[Any], market: Market) -> dict[str, Any]:
        granularities = sorted({metric.market_granularity or "unknown" for metric in metrics})
        country_level = any(item in {"country", "national"} for item in granularities)
        total_volume = sum(metric.search_volume or 0 for metric in metrics)
        warning = None
        if country_level:
            warning = (
                "Keyword volume is provider-reported at country level. Treat it as service demand "
                "evidence, not exact city demand, until local estimation is implemented."
            )
        return {
            "keyword_metric_granularities": granularities,
            "provider_reported_metric_granularity": "mixed" if len(granularities) > 1 else (granularities[0] if granularities else "none"),
            "national_service_demand": total_volume if country_level else None,
            "estimated_market_demand": None,
            "market_estimation_method": "not_estimated",
            "market_estimation_confidence": "none",
            "localized_competition": bool(market.provider_location_code or market.provider_location_name or market.latitude),
            "localized_provider_supply": bool(market.latitude and market.longitude),
            "warning": warning,
        }

    @property
    def serp_keyword_limit(self) -> int:
        if self.data_mode != DataMode.live:
            return 3
        return 1 if self.live_scan_depth == "testing" else 3

    @property
    def backlink_competitor_limit(self) -> int:
        if self.data_mode != DataMode.live:
            return 5
        return 0 if self.live_scan_depth == "testing" else 5

    @property
    def estimated_paid_api_calls(self) -> int:
        if self.data_mode != DataMode.live:
            return 0
        keyword_suggestion_calls = 1 if self.live_scan_depth == "testing" else 3
        keyword_metrics_calls = 1
        serp_calls = self.serp_keyword_limit
        backlink_calls = self.backlink_competitor_limit
        business_listing_calls = 1
        return (
            keyword_suggestion_calls
            + keyword_metrics_calls
            + serp_calls
            + backlink_calls
            + business_listing_calls
        )

    @property
    def is_preliminary_assessment(self) -> bool:
        return self.data_mode == DataMode.live and self.live_scan_depth == "testing"

    @property
    def unavailable_components(self) -> list[str]:
        return ["backlink_competitor_metrics", "full_serp_sample"] if self.is_preliminary_assessment else []

    @property
    def additional_calls_required_for_full_scan(self) -> int:
        return 7 if self.is_preliminary_assessment else 0

    def _actual_api_cost_for_scan(self, scan_run_id: int | None) -> float:
        if self.data_mode != DataMode.live or scan_run_id is None:
            return 0.0
        total = 0.0
        rows = self.session.scalars(
            select(ApiCallORM).where(ApiCallORM.scan_run_id == scan_run_id)
        ).all()
        for row in rows:
            total += row.actual_cost_usd or 0.0
        return round(total, 6)


def score_summary(score: OpportunityScore) -> str:
    return f"{score.total_score} ({score.confidence.value}) - {score.explanation}"
