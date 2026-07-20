from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.orm import Session

from rank_rent.db.orm import (
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
from rank_rent.observability.logging import log_event
from rank_rent.observability.metrics import (
    COST_RECONCILIATION_FAILURES,
    DISCOVERY_CONFIDENCE,
    DISCOVERY_SCANS,
    EVIDENCE_GATE_RESULTS,
    SCORE_VERSIONS,
    WORKER_STAGE_DURATION,
)
from rank_rent.opportunity_review.models import OpportunityState
from rank_rent.opportunity_review.services import (
    OpportunityReviewService,
    require_property_approval,
)
from rank_rent.planning import ScanPlan, build_scan_plan
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
from rank_rent.services.competitors import enrich_competitors, select_competitor_urls
from rank_rent.services.demand import analyze_demand
from rank_rent.services.discovery_report import (
    build_api_cost_ledger,
    build_discovery_report,
)
from rank_rent.services.domains import generate_domain_candidates
from rank_rent.services.evidence_quality import EvidenceQualityEvaluator
from rank_rent.services.keywords import (
    plan_keyword_candidates,
    rank_and_cluster_keyword_metrics,
    service_keyword_terms,
)
from rank_rent.services.market_prefilter import MarketPrefilter
from rank_rent.services.outreach import generate_initial_email
from rank_rent.services.providers import score_provider_suitability
from rank_rent.services.records import save_scan_plan_calls, save_scan_records
from rank_rent.services.scan_leases import (
    ScanExecutionLease,
    ScanLeaseLost,
    assert_current_scan_lease,
)
from rank_rent.services.service_catalog import load_service_catalog
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
        scan_profile: str | None = None,
        execution_lease: ScanExecutionLease | None = None,
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
        self.scan_profile = _scan_profile(
            scan_profile or self.settings.live_scan_depth
        )
        self.execution_lease = execution_lease
        if self.data_mode == DataMode.live:
            provider = cast(Any, self.research_provider)
            provider.scan_profile_override = self.scan_profile
            provider.execution_lease = execution_lease
        self.scorer = OpportunityScorer()
        self.evidence_quality = EvidenceQualityEvaluator(
            self.settings.project_root / "config/evidence_quality.yaml"
        )
        self._current_stage: str | None = None
        self._stage_started_at: float | None = None

    async def run(
        self,
        service: ServiceFamily,
        market: Market,
        *,
        source: str = "manual",
        build_site: bool = False,
        existing_scan_id: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_lease()
        public_data_prefilter = MarketPrefilter.from_settings(
            self.settings
        ).assess_market(service, market)
        public_data_prefilter_payload = (
            public_data_prefilter.model_dump(mode="json")
            if public_data_prefilter
            else None
        )
        plan = build_scan_plan(
            self.settings,
            self.data_mode,
            service,
            market,
            session=self.session,
            scan_profile=self.scan_profile,
        )
        if plan.blocked:
            raise RuntimeError(plan.block_reason or "Scan blocked by cost policy.")
        service_row = upsert_service(self.session, service)
        market_row = upsert_market(self.session, market)
        opportunity = get_or_create_opportunity(self.session, service_row, market_row)
        if build_site:
            require_property_approval(self.session, opportunity.id)
        review = OpportunityReviewService(self.session)
        review.transition_system(
            opportunity.id,
            (
                OpportunityState.testing_running
                if self.is_preliminary_assessment
                else OpportunityState.full_running
            ),
            decision="scan_started",
            reason=f"{self.scan_profile.title()} scan execution started.",
        )
        scan = self._prepare_scan_run(
            existing_scan_id=existing_scan_id,
            opportunity_id=opportunity.id,
            source=source,
            service=service,
            market=market,
            plan=plan,
            public_data_prefilter=public_data_prefilter_payload,
        )
        self.session.flush()
        if hasattr(self.research_provider, "current_scan_run_id"):
            self.research_provider.current_scan_run_id = scan.id
        save_scan_plan_calls(self.session, scan.id, plan)

        try:
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
                snapshot.results = [
                    classify_result(result, service=service, market=market)
                    for result in snapshot.results
                ]
                serp_snapshots.append(snapshot)
            competitor_urls = select_competitor_urls(
                serp_snapshots,
                self.backlink_competitor_limit,
            )
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
                self.scorer.config["providers"],
            )
            for snapshot in serp_snapshots:
                snapshot.results = [
                    classify_result(result, service=service, market=market, providers=providers)
                    for result in snapshot.results
                ]
            self._ensure_not_cancelled(scan)
            self._set_stage(scan, "scoring")
            is_preliminary = self.is_preliminary_assessment
            assessment_type = "preliminary" if is_preliminary else "full"
            evidence_quality = self.evidence_quality.assess(
                service=service,
                metrics=keyword_metrics.scoring_metrics,
                serp_snapshots=serp_snapshots,
                competitors=competitors,
                providers=providers,
                assessment_type=assessment_type,
                service_configured=self._service_is_configured(service),
            )
            score = self.scorer.score(
                keyword_metrics.scoring_metrics,
                serp_snapshots,
                competitors,
                providers,
                market,
                source_mode=self.evidence_source_mode,
                assessment_type=assessment_type,
            )
            score = self.evidence_quality.apply_to_score(score, evidence_quality)
            evidence_quality_payload = evidence_quality.model_dump(mode="json")
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "evidence_quality": evidence_quality_payload,
            }
            self._set_stage(scan, "persisting_results")
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

            self._ensure_not_cancelled(scan)
            review.transition_system(
                opportunity.id,
                (
                    OpportunityState.needs_more_evidence
                    if evidence_quality.status == "fail"
                    or (not is_preliminary and score.evidence_status != "complete")
                    else OpportunityState.preliminary_review
                    if is_preliminary
                    else OpportunityState.full_review
                ),
                decision="scan_completed",
                reason=(
                    "Scan completed but evidence requires remediation."
                    if evidence_quality.status == "fail"
                    or (not is_preliminary and score.evidence_status != "complete")
                    else f"{assessment_type.title()} evidence is ready for review."
                ),
            )
            if is_preliminary:
                if opportunity.latest_score is None:
                    opportunity.confidence = (
                        "insufficient"
                        if evidence_quality.status == "fail"
                        else "preliminary"
                    )
                    opportunity.score_version = score.scoring_version
            elif score.evidence_status == "complete":
                opportunity.latest_score = score.total_score
                opportunity.score_version = score.scoring_version
                opportunity.confidence = score.confidence.value
            elif opportunity.latest_score is None:
                opportunity.score_version = score.scoring_version
                opportunity.confidence = score.confidence.value
            opportunity.missing_data_flags = score.missing_fields
            scan.status = "completed"
            self._observe_current_stage()
            scan.progress_stage = "completed"
            scan.completed_at = datetime.now(UTC)
            cost_ledger = build_api_cost_ledger(self.session, scan.id)
            if not bool(cost_ledger["ledger_complete"]):
                COST_RECONCILIATION_FAILURES.inc()
            scan.actual_cost_usd = float(cost_ledger["actual_cost_usd"])
            scan.scoring_version = score.scoring_version
            scan_metadata = {
                "scan_run_id": scan.id,
                "data_mode": self.data_mode.value,
                "evidence_source_mode": self.evidence_source_mode,
                "scan_profile": scan.scan_profile,
                "planned_cost_usd": scan.planned_cost_usd,
                "actual_cost_usd": scan.actual_cost_usd,
                "estimated_paid_api_calls": self.estimated_paid_api_calls,
                "started_at": scan.started_at.isoformat() if scan.started_at else None,
                "completed_at": scan.completed_at.isoformat()
                if scan.completed_at
                else None,
                "adapter_names": scan.adapter_names,
                "adapter_versions": scan.adapter_versions,
                "normalization_version": scan.normalization_version,
                "cache_policy_version": scan.cache_policy_version,
                "api_cost_ledger": cost_ledger,
                "evidence_quality": evidence_quality_payload,
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
                demand_estimator=self.scorer.market_demand_estimator,
                public_data_prefilter=public_data_prefilter_payload,
                evidence_quality=evidence_quality_payload,
            )

            save_artifact(
                self.session,
                opportunity.id,
                "preliminary_assessment" if is_preliminary else "scan_result",
                {
                    "data_mode": self.data_mode.value,
                    "evidence_source_mode": self.evidence_source_mode,
                    "live_scan_depth": self.scan_profile if self.data_mode == DataMode.live else None,
                    "estimated_paid_api_calls": self.estimated_paid_api_calls,
                    "assessment_type": "preliminary" if is_preliminary else "full",
                    "unavailable_components": self.unavailable_components,
                    "additional_calls_required_for_full_scan": self.additional_calls_required_for_full_scan,
                    "demand_evidence": self._demand_evidence(metrics, market),
                    "public_data_prefilter": public_data_prefilter_payload,
                    "evidence_quality": evidence_quality_payload,
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
                scan_run_id=scan.id,
            )
            save_artifact(
                self.session,
                opportunity.id,
                "discovery_report",
                discovery_report,
                scan_run_id=scan.id,
            )
            self._save_assessment_records(scan, opportunity.id, score, is_preliminary)
            if build_site and site_config:
                save_artifact(
                    self.session,
                    opportunity.id,
                    "domain_candidates",
                    {"domains": [d.model_dump(mode="json") for d in domains]},
                    scan_run_id=scan.id,
                )
                save_artifact(
                    self.session,
                    opportunity.id,
                    "outreach_drafts",
                    {"drafts": outreach},
                    scan_run_id=scan.id,
                )
                save_artifact(
                    self.session,
                    opportunity.id,
                    "site_config",
                    {
                        "config": site_config.model_dump(mode="json"),
                        "generated_path": str(site_path) if site_path else None,
                    },
                    scan_run_id=scan.id,
                )
            self._ensure_lease(lock=True)
            self.session.commit()
            DISCOVERY_SCANS.labels(profile=scan.scan_profile, status="completed").inc()
            EVIDENCE_GATE_RESULTS.labels(status=evidence_quality.status).inc()
            DISCOVERY_CONFIDENCE.labels(confidence=score.confidence.value).inc()
            SCORE_VERSIONS.labels(version=score.scoring_version).inc()
            return {
                "opportunity_id": opportunity.id,
                "scan_id": scan.id,
                "data_mode": self.data_mode.value,
                "score": score,
                "assessment_type": "preliminary" if is_preliminary else "full",
                "scan_plan": plan,
                "domains": domains,
                "providers": providers,
                "public_data_prefilter": public_data_prefilter,
                "evidence_quality": evidence_quality,
                "site_path": site_path,
            }
        except ScanLeaseLost:
            self.session.rollback()
            raise
        except ScanCancelled:
            self._observe_current_stage()
            scan.status = "cancelled"
            scan.progress_stage = "cancelled"
            scan.completed_at = datetime.now(UTC)
            scan.actual_cost_usd = float(
                build_api_cost_ledger(self.session, scan.id)["actual_cost_usd"]
            )
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "cancelled": True,
            }
            review.transition_system(
                opportunity.id,
                OpportunityState.needs_more_evidence,
                decision="scan_cancelled",
                reason="Scan was cancelled before assessment completion.",
            )
            self.session.commit()
            DISCOVERY_SCANS.labels(profile=scan.scan_profile, status="cancelled").inc()
            raise
        except Exception as exc:
            self._observe_current_stage()
            failed_stage = scan.progress_stage
            scan.status = "failed"
            scan.progress_stage = "failed"
            scan.error_summary = str(exc)
            scan.completed_at = datetime.now(UTC)
            scan.actual_cost_usd = float(
                build_api_cost_ledger(self.session, scan.id)["actual_cost_usd"]
            )
            review.transition_system(
                opportunity.id,
                OpportunityState.needs_more_evidence,
                decision="scan_failed",
                reason=f"Scan failed during {failed_stage}: {exc}",
            )
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "failed_stage": failed_stage,
                "error": str(exc),
            }
            self.session.commit()
            DISCOVERY_SCANS.labels(profile=scan.scan_profile, status="failed").inc()
            raise

    def _prepare_scan_run(
        self,
        *,
        existing_scan_id: int | None,
        opportunity_id: int,
        source: str,
        service: ServiceFamily,
        market: Market,
        plan: ScanPlan,
        public_data_prefilter: dict[str, Any] | None,
    ) -> ScanRunORM:
        self._ensure_lease()
        scan = self.session.get(ScanRunORM, existing_scan_id) if existing_scan_id else None
        if scan is None:
            scan = ScanRunORM(opportunity_id=opportunity_id, source=source)
            self.session.add(scan)
        scan.opportunity_id = opportunity_id
        scan.source = source
        scan.status = "running"
        scan.data_mode = self.data_mode.value
        scan.scan_profile = self.scan_profile
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
        self._current_stage = "planning"
        self._stage_started_at = time.perf_counter()
        scan.estimated_cost_usd = float(plan.estimated_uncached_cost_usd)
        scan.actual_cost_usd = 0
        scan.started_at = datetime.now(UTC)
        scan.completed_at = None
        scan.error_summary = None
        scan.integration_versions = {
            "data_mode": self.data_mode.value,
            "evidence_source_mode": self.evidence_source_mode,
            "dataforseo_environment": (
                self.settings.dataforseo_environment
                if self.data_mode == DataMode.live
                else None
            ),
            "market_research_provider": getattr(
                self.research_provider,
                "provider_name",
                type(self.research_provider).__name__,
            ),
            "domain_provider": type(self.domain_provider).__name__,
            "live_scan_depth": self.scan_profile if self.data_mode == DataMode.live else None,
            "cache_policy_version": "v2",
        }
        scan.request_parameters = {
            "service": service.slug,
            "market": market.slug,
            "data_mode": self.data_mode.value,
            "scan_profile": self.scan_profile,
            "evidence_source_mode": self.evidence_source_mode,
            "dataforseo_environment": (
                self.settings.dataforseo_environment
                if self.data_mode == DataMode.live
                else None
            ),
            "live_scan_depth": self.scan_profile if self.data_mode == DataMode.live else None,
            "scan_plan": plan.model_dump(mode="json"),
            "service_payload": service.model_dump(mode="json"),
            "market_payload": market.model_dump(mode="json"),
            "final_market_payload": market.model_dump(mode="json"),
            "public_data_prefilter": public_data_prefilter,
        }
        return scan

    def _ensure_not_cancelled(self, scan: ScanRunORM) -> None:
        self._ensure_lease()
        self.session.refresh(scan)
        if scan.cancel_requested:
            raise ScanCancelled(f"Scan {scan.id} was cancelled.")

    def _set_stage(self, scan: ScanRunORM, stage: str) -> None:
        self._observe_current_stage()
        outputs = {
            **(scan.partial_outputs or {}),
            "last_successful_stage": stage,
        }
        if self.execution_lease is not None:
            now = datetime.now(UTC)
            result = self.session.execute(
                update(ScanRunORM)
                .where(
                    ScanRunORM.id == self.execution_lease.scan_id,
                    ScanRunORM.status == "running",
                    ScanRunORM.worker_id == self.execution_lease.worker_id,
                    ScanRunORM.lease_token == self.execution_lease.lease_token,
                    ScanRunORM.lease_expires_at.is_not(None),
                    ScanRunORM.lease_expires_at > now,
                )
                .values(progress_stage=stage, partial_outputs=outputs)
            )
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                self.session.rollback()
                raise ScanLeaseLost(
                    f"Worker lease was lost before scan {scan.id} could enter stage {stage}."
                )
        else:
            scan.progress_stage = stage
            scan.partial_outputs = outputs
        self.session.commit()
        self.session.refresh(scan)
        self._current_stage = stage
        self._stage_started_at = time.perf_counter()
        log_event(
            "scan.stage.started",
            scan_run_id=scan.id,
            opportunity_id=scan.opportunity_id,
            stage=stage,
        )

    def _observe_current_stage(self) -> None:
        if self._current_stage is None or self._stage_started_at is None:
            return
        WORKER_STAGE_DURATION.labels(stage=self._current_stage).observe(
            time.perf_counter() - self._stage_started_at
        )
        self._current_stage = None
        self._stage_started_at = None

    def _ensure_lease(self, *, lock: bool = False) -> None:
        if self.execution_lease is not None:
            assert_current_scan_lease(
                self.session,
                self.execution_lease,
                lock=lock,
            )

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
            detail = score.component_details.get(component)
            self.session.add(
                ScoreComponentORM(
                    scan_run_id=scan.id,
                    component=component,
                    score=value,
                    inputs={
                        "measurements": detail.inputs,
                        "calculation_steps": [
                            step.model_dump(mode="json")
                            for step in detail.calculation_steps
                        ],
                        "maximum_score": detail.maximum_score,
                        "explanation": detail.explanation,
                    }
                    if detail
                    else {},
                    formula=detail.formula if detail else "",
                    penalties={},
                )
            )

    def _demand_evidence(self, metrics: list[Any], market: Market) -> dict[str, Any]:
        evidence = analyze_demand(
            metrics,
            market,
            estimator=self.scorer.market_demand_estimator,
        )
        if evidence["national_service_demand"] is not None:
            warning = (
                "Keyword volume is provider-reported at country level. It supports service "
                "attractiveness, while market attractiveness requires measured or transparently "
                "estimated local demand."
            )
        elif evidence["provider_reported_local_demand"] is not None:
            warning = (
                "Local keyword volume supports market attractiveness only. Service "
                "attractiveness receives no demand points without independent national evidence."
            )
        else:
            warning = None
        return {
            **evidence,
            "localized_competition": bool(
                market.provider_location_code
                or market.provider_location_name
                or (market.latitude is not None and market.longitude is not None)
            ),
            "localized_provider_supply": bool(
                market.latitude is not None
                and market.longitude is not None
                and market.boundary_radius_km is not None
                and market.boundary_radius_km > 0
            ),
            "warning": warning,
        }

    def _service_is_configured(self, service: ServiceFamily) -> bool:
        catalog = load_service_catalog(
            self.settings.project_root / "config/services.yaml"
        )
        return (
            catalog.resolve(service.id) is not None
            or catalog.resolve(service.display_name) is not None
        )

    @property
    def evidence_source_mode(self) -> str:
        if self.data_mode == DataMode.live:
            return (
                "live"
                if self.settings.dataforseo_environment.strip().lower() == "production"
                else "sandbox"
            )
        return self.data_mode.value

    @property
    def serp_keyword_limit(self) -> int:
        if self.data_mode != DataMode.live:
            return 3
        return 1 if self.scan_profile == "testing" else 3

    @property
    def backlink_competitor_limit(self) -> int:
        if self.data_mode != DataMode.live:
            return 5
        return 0 if self.scan_profile == "testing" else 5

    @property
    def estimated_paid_api_calls(self) -> int:
        if self.data_mode != DataMode.live:
            return 0
        keyword_suggestion_calls = 1 if self.scan_profile == "testing" else 3
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
        return self.data_mode == DataMode.live and self.scan_profile == "testing"

    @property
    def unavailable_components(self) -> list[str]:
        return ["backlink_competitor_metrics", "full_serp_sample"] if self.is_preliminary_assessment else []

    @property
    def additional_calls_required_for_full_scan(self) -> int:
        return 7 if self.is_preliminary_assessment else 0

def score_summary(score: OpportunityScore) -> str:
    return f"{score.total_score} ({score.confidence.value}) - {score.explanation}"


def _scan_profile(value: str) -> str:
    profile = value.lower().strip()
    if profile not in {"testing", "full"}:
        raise ValueError("scan_profile must be 'testing' or 'full'.")
    return profile
