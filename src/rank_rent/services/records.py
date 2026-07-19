from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rank_rent.db.orm import (
    CompetitorMetricORM,
    KeywordClusterORM,
    KeywordDecisionORM,
    KeywordMetricORM,
    ProviderCandidateORM,
    ScanPlanCallORM,
    ScanPlanORM,
    SerpResultORM,
    SerpSnapshotORM,
)
from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    ProviderCandidate,
    SerpSnapshot,
)
from rank_rent.planning import ScanPlan
from rank_rent.services.keywords import KeywordCluster, KeywordDecision


def save_scan_plan_calls(session: Session, scan_run_id: int, plan: ScanPlan) -> None:
    session.execute(delete(ScanPlanCallORM).where(ScanPlanCallORM.scan_run_id == scan_run_id))
    session.execute(delete(ScanPlanORM).where(ScanPlanORM.scan_run_id == scan_run_id))
    session.add(
        ScanPlanORM(
            scan_run_id=scan_run_id,
            scan_profile=plan.scan_profile,
            cache_hit_count=plan.cache_hit_count,
            paid_call_count=plan.paid_call_count,
            estimated_uncached_cost_usd=float(plan.estimated_uncached_cost_usd),
            maximum_allowed_cost_usd=float(plan.maximum_allowed_cost_usd),
            confirmation_required=plan.confirmation_required,
            blocked=plan.blocked,
            block_reason=plan.block_reason,
        )
    )
    for call in plan.planned_calls:
        session.add(
            ScanPlanCallORM(
                scan_run_id=scan_run_id,
                planned_request_id=call.planned_request_id,
                provider=call.provider,
                endpoint=call.endpoint,
                stage=call.stage,
                request_parameters=call.request_parameters,
                cache_key=call.cache_key,
                cache_hit=call.cache_hit,
                request_known=call.request_known,
                estimated_cost_usd=float(call.estimated_cost_usd),
                required=call.required,
            )
        )
    session.flush()


def save_scan_records(
    session: Session,
    *,
    scan_run_id: int,
    opportunity_id: int | None,
    metrics: list[KeywordMetric],
    serp_snapshots: list[SerpSnapshot],
    competitors: list[CompetitorMetric],
    providers: list[ProviderCandidate],
    keyword_clusters: list[KeywordCluster] | None = None,
    keyword_decisions: list[KeywordDecision] | None = None,
) -> None:
    _clear_scan_records(session, scan_run_id)
    for cluster in keyword_clusters or []:
        session.add(
            KeywordClusterORM(
                scan_run_id=scan_run_id,
                representative_keyword=cluster.representative_keyword,
                keywords=cluster.keywords,
                dedupe_method=cluster.dedupe_method,
                combined_volume=cluster.combined_volume,
            )
        )
    for decision in keyword_decisions or []:
        session.add(
            KeywordDecisionORM(
                scan_run_id=scan_run_id,
                keyword=decision.keyword,
                canonical_keyword=decision.canonical_keyword,
                decision=decision.decision,
                reason=decision.reason,
                rank=decision.rank,
                representative=decision.representative,
                cluster_id=decision.cluster_id,
                intent=decision.intent,
                search_volume=decision.search_volume,
                cpc=decision.cpc,
                granularity=decision.granularity,
                ranking_score=decision.ranking_score,
            )
        )
    for metric in metrics:
        session.add(
            KeywordMetricORM(
                scan_run_id=scan_run_id,
                opportunity_id=opportunity_id,
                keyword=metric.keyword,
                canonical_keyword=metric.canonical_keyword,
                intent=metric.intent,
                search_volume=metric.search_volume,
                cpc=metric.cpc,
                paid_competition=metric.paid_competition,
                monthly_history=metric.monthly_history,
                source=metric.source,
                source_timestamp=metric.source_timestamp,
                market_granularity=metric.market_granularity,
                included=metric.included,
                excluded_reason=metric.excluded_reason,
            )
        )

    for snapshot in serp_snapshots:
        row = SerpSnapshotORM(
            scan_run_id=scan_run_id,
            opportunity_id=opportunity_id,
            query=snapshot.query,
            market_id=snapshot.market_id,
            device=snapshot.device,
            captured_at=snapshot.captured_at,
            features_present=snapshot.features_present,
            raw_response_ref=snapshot.raw_response_ref,
        )
        session.add(row)
        session.flush()
        for result in snapshot.results:
            session.add(
                SerpResultORM(
                    serp_snapshot_id=row.id,
                    order=result.order,
                    result_type=result.result_type,
                    url=result.url,
                    domain=result.domain,
                    title=result.title,
                    description=result.description,
                    classification=result.classification,
                    is_local_provider=result.is_local_provider,
                    is_directory=result.is_directory,
                    is_national_brand=result.is_national_brand,
                    is_lead_generation_site=result.is_lead_generation_site,
                    classification_confidence=result.classification_confidence,
                    classifier_version=result.classifier_version,
                    matched_rules=result.matched_rules,
                    classification_evidence=result.classification_evidence,
                    manual_override=result.manual_override,
                    override_reason=result.override_reason,
                )
            )

    for competitor in competitors:
        session.add(
            CompetitorMetricORM(
                scan_run_id=scan_run_id,
                opportunity_id=opportunity_id,
                url=competitor.url,
                domain=competitor.domain,
                referring_domains=competitor.referring_domains,
                backlinks=competitor.backlinks,
                authority=competitor.authority,
                page_relevance_score=competitor.page_relevance_score,
                local_relevance=competitor.local_relevance,
                page_type=competitor.page_type,
                relevance_signals=competitor.relevance_signals,
                captured_at=competitor.captured_at,
            )
        )

    for provider in providers:
        session.add(
            ProviderCandidateORM(
                scan_run_id=scan_run_id,
                opportunity_id=opportunity_id,
                name=provider.name,
                website=provider.website,
                phone=provider.phone,
                email=provider.email,
                contact_form_url=provider.contact_form_url,
                address=provider.address,
                service_area=provider.service_area,
                category=provider.category,
                categories=provider.categories,
                latitude=provider.latitude,
                longitude=provider.longitude,
                rating=provider.rating,
                review_count=provider.review_count,
                business_status=provider.business_status,
                contact_confidence=provider.contact_confidence,
                source=provider.source,
                source_timestamp=provider.source_timestamp,
                raw_response_ref=provider.raw_response_ref,
                outreach_status=provider.outreach_status,
                suitability_score=provider.suitability_score,
                suitability_reasons=provider.suitability_reasons,
                suitability_signals=provider.suitability_signals,
            )
        )
    session.flush()


def _clear_scan_records(session: Session, scan_run_id: int) -> None:
    snapshot_ids = select(SerpSnapshotORM.id).where(SerpSnapshotORM.scan_run_id == scan_run_id)
    session.execute(delete(SerpResultORM).where(SerpResultORM.serp_snapshot_id.in_(snapshot_ids)))
    session.execute(delete(SerpSnapshotORM).where(SerpSnapshotORM.scan_run_id == scan_run_id))
    session.execute(delete(KeywordDecisionORM).where(KeywordDecisionORM.scan_run_id == scan_run_id))
    session.execute(delete(KeywordClusterORM).where(KeywordClusterORM.scan_run_id == scan_run_id))
    session.execute(delete(KeywordMetricORM).where(KeywordMetricORM.scan_run_id == scan_run_id))
    session.execute(delete(CompetitorMetricORM).where(CompetitorMetricORM.scan_run_id == scan_run_id))
    session.execute(delete(ProviderCandidateORM).where(ProviderCandidateORM.scan_run_id == scan_run_id))
