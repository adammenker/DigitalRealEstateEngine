from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from rank_rent.db.orm import ScanRunORM
from rank_rent.domain.interfaces import DomainAvailabilityProvider, MarketResearchProvider
from rank_rent.domain.models import Market, OpportunityScore, ServiceFamily
from rank_rent.integrations.factory import (
    build_domain_availability_provider,
    build_market_research_provider,
)
from rank_rent.repositories import (
    get_or_create_opportunity,
    save_artifact,
    upsert_market,
    upsert_service,
)
from rank_rent.runtime import DataMode, resolve_data_mode
from rank_rent.scoring.score import OpportunityScorer
from rank_rent.scoring.serp import classify_result
from rank_rent.services.domains import generate_domain_candidates
from rank_rent.services.keywords import dedupe_and_filter_keywords
from rank_rent.services.outreach import generate_initial_email
from rank_rent.settings import get_settings
from rank_rent.site_generator.generator import build_site_config, generate_static_site


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
        self.research_provider = research_provider or build_market_research_provider(
            self.settings,
            self.data_mode,
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
        build_site: bool = True,
    ) -> dict[str, Any]:
        if (
            self.data_mode == DataMode.live
            and not market.provider_location_code
            and not market.provider_location_name
        ):
            market = (await self.research_provider.resolve_location(market.display_name)).market
        service_row = upsert_service(self.session, service)
        market_row = upsert_market(self.session, market)
        opportunity = get_or_create_opportunity(self.session, service_row, market_row)
        scan = ScanRunORM(
            opportunity_id=opportunity.id,
            source=source,
            status="running",
            estimated_cost_usd=0 if source == "fixture" else 2.5,
            started_at=datetime.now(UTC),
            integration_versions={
                "data_mode": self.data_mode.value,
                "market_research_provider": getattr(
                    self.research_provider,
                    "provider_name",
                    type(self.research_provider).__name__,
                ),
                "domain_provider": type(self.domain_provider).__name__,
                "live_scan_depth": self.live_scan_depth if self.data_mode == DataMode.live else None,
            },
            request_parameters={
                "service": service.slug,
                "market": market.slug,
                "data_mode": self.data_mode.value,
                "live_scan_depth": self.live_scan_depth if self.data_mode == DataMode.live else None,
            },
        )
        self.session.add(scan)
        self.session.flush()

        try:
            candidates = await self.research_provider.discover_keywords(service, market)
            keywords = dedupe_and_filter_keywords(candidates, service.negative_terms)
            included_keywords = [k.keyword for k in keywords if k.included]
            metrics = await self.research_provider.get_keyword_metrics(included_keywords, market)
            representative = included_keywords[: self.serp_keyword_limit]
            serp_snapshots = []
            for keyword in representative:
                snapshot = await self.research_provider.get_serp_snapshot(keyword, market)
                snapshot.results = [classify_result(result) for result in snapshot.results]
                serp_snapshots.append(snapshot)
            competitor_urls = [
                r.url for s in serp_snapshots for r in s.results if r.result_type == "organic"
            ][: self.backlink_competitor_limit]
            competitors = (
                await self.research_provider.get_competitor_metrics(competitor_urls)
                if competitor_urls
                else []
            )
            providers = await self.research_provider.find_providers(service, market)
            score = self.scorer.score(metrics, serp_snapshots, competitors, providers)
            domains = await generate_domain_candidates(service, market, self.domain_provider)
            outreach = [
                generate_initial_email(provider, service, market).model_dump(mode="json")
                for provider in providers[:2]
            ]
            site_config = build_site_config(service, market, domains[0].domain if domains else None)
            site_path: Path | None = generate_static_site(site_config) if build_site else None

            save_artifact(
                self.session,
                opportunity.id,
                "scan_result",
                {
                    "data_mode": self.data_mode.value,
                    "live_scan_depth": self.live_scan_depth if self.data_mode == DataMode.live else None,
                    "estimated_paid_api_calls": self.estimated_paid_api_calls,
                    "keywords": [k.model_dump(mode="json") for k in keywords],
                    "metrics": [m.model_dump(mode="json") for m in metrics],
                    "serp_snapshots": [s.model_dump(mode="json") for s in serp_snapshots],
                    "competitors": [c.model_dump(mode="json") for c in competitors],
                    "providers": [p.model_dump(mode="json") for p in providers],
                    "score": score.model_dump(mode="json"),
                },
            )
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
            opportunity.status = "review_required"
            opportunity.latest_score = score.total_score
            opportunity.score_version = score.scoring_version
            opportunity.confidence = score.confidence.value
            opportunity.missing_data_flags = score.missing_fields
            scan.status = "completed"
            scan.completed_at = datetime.now(UTC)
            self.session.commit()
            return {
                "opportunity_id": opportunity.id,
                "scan_id": scan.id,
                "data_mode": self.data_mode.value,
                "score": score,
                "domains": domains,
                "providers": providers,
                "site_path": site_path,
            }
        except Exception as exc:
            scan.status = "failed"
            scan.error_summary = str(exc)
            scan.completed_at = datetime.now(UTC)
            opportunity.status = "review_required"
            self.session.commit()
            raise

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


def score_summary(score: OpportunityScore) -> str:
    return f"{score.total_score} ({score.confidence.value}) - {score.explanation}"
