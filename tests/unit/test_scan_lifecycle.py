from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import (
    KeywordMetricORM,
    OpportunityORM,
    ProviderCandidateORM,
    ScanPlanCallORM,
    ScanRunORM,
    SerpSnapshotORM,
)
from rank_rent.domain.models import (
    AvailabilityStatus,
    CompetitorMetric,
    DomainAvailabilityResult,
    KeywordCandidate,
    KeywordMetric,
    Market,
    ProviderCandidate,
    ResolvedLocation,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
)
from rank_rent.repositories import get_or_create_opportunity, upsert_market, upsert_service
from rank_rent.services.scanner import ScanPipeline
from rank_rent.settings import get_settings


class MinimalResearchProvider:
    provider_name = "minimal-test-provider"

    async def resolve_location(self, query: str) -> ResolvedLocation:
        market = Market(id=query, display_name=query, provider_location_code="test-location")
        return ResolvedLocation(
            original_input=query,
            market=market,
            provider_location_code="test-location",
            provider_location_name=query,
            granularity="city",
        )

    async def discover_keywords(self, service: ServiceFamily, market: Market) -> list[KeywordCandidate]:
        return [KeywordCandidate(keyword=f"{service.display_name} repair")]

    async def get_keyword_metrics(self, keywords: list[str], market: Market) -> list[KeywordMetric]:
        return [
            KeywordMetric(
                keyword=keywords[0],
                canonical_keyword=keywords[0],
                intent="commercial",
                search_volume=100,
                cpc=10,
                source="test-live",
            )
        ]

    async def get_serp_snapshot(self, keyword: str, market: Market) -> SerpSnapshot:
        return SerpSnapshot(
            query=keyword,
            market_id=market.id,
            results=[
                SerpResult(
                    order=1,
                    url="https://local.example",
                    domain="local.example",
                    title="Local Contractor",
                )
            ],
        )

    async def get_competitor_metrics(self, urls: list[str]) -> list[CompetitorMetric]:
        return []

    async def find_providers(
        self, service: ServiceFamily, market: Market
    ) -> list[ProviderCandidate]:
        return [ProviderCandidate(name="Local Co", phone="555-0100", business_status="open")]


class StaticDomainProvider:
    async def check(self, domain: str) -> DomainAvailabilityResult:
        return DomainAvailabilityResult(domain=domain, status=AvailabilityStatus.unknown)


class FailingLocationProvider(MinimalResearchProvider):
    async def resolve_location(self, query: str) -> ResolvedLocation:
        raise RuntimeError("location unavailable")


def test_preliminary_scan_does_not_clear_existing_full_score(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis_mo", display_name="St. Louis, MO")

    with Session() as session:
        service_row = upsert_service(session, service)
        market_row = upsert_market(session, market)
        opportunity = get_or_create_opportunity(session, service_row, market_row)
        opportunity.latest_score = 77.0
        opportunity.confidence = "high"
        opportunity.score_version = "v1"
        session.commit()

        result = asyncio.run(
            ScanPipeline(
                session,
                research_provider=MinimalResearchProvider(),
                domain_provider=StaticDomainProvider(),
                data_mode="live",
            ).run(service, market, source="manual")
        )

        assert result["assessment_type"] == "preliminary"
        assert opportunity.latest_score == 77.0
        assert opportunity.confidence == "high"


def test_failed_live_location_resolution_is_persisted(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis_mo", display_name="St. Louis, MO")

    with Session() as session:
        with pytest.raises(RuntimeError, match="location unavailable"):
            asyncio.run(
                ScanPipeline(
                    session,
                    research_provider=FailingLocationProvider(),
                    domain_provider=StaticDomainProvider(),
                    data_mode="live",
                ).run(service, market, source="manual")
            )

        scan = session.scalar(select(ScanRunORM))
        assert scan is not None
        assert scan.status == "failed"
        assert scan.error_summary == "location unavailable"
        assert scan.opportunity_id is not None
        opportunity = session.get(OpportunityORM, scan.opportunity_id)
        assert opportunity is not None
        assert opportunity.status == "scan_failed"


def test_scan_reuses_queued_row_and_writes_typed_records(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis_mo", display_name="St. Louis, MO")

    with Session() as session:
        queued = ScanRunORM(source="manual_async", status="queued")
        session.add(queued)
        session.commit()

        result = asyncio.run(
            ScanPipeline(
                session,
                research_provider=MinimalResearchProvider(),
                domain_provider=StaticDomainProvider(),
                data_mode="live",
            ).run(service, market, source="manual_async", existing_scan_id=queued.id)
        )

        assert result["scan_id"] == queued.id
        scan = session.get(ScanRunORM, queued.id)
        assert scan is not None
        assert scan.status == "completed"
        assert len(session.scalars(select(ScanPlanCallORM)).all()) == 5
        assert len(session.scalars(select(KeywordMetricORM)).all()) == 1
        assert len(session.scalars(select(SerpSnapshotORM)).all()) == 1
        assert len(session.scalars(select(ProviderCandidateORM)).all()) == 1
