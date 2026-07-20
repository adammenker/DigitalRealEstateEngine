from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import (
    CompetitorMetricORM,
    KeywordClusterORM,
    KeywordDecisionORM,
    KeywordMetricORM,
    ProviderCandidateORM,
    ScanPlanCallORM,
    ScanRunORM,
    SerpSnapshotORM,
)
from rank_rent.domain.models import (
    AvailabilityStatus,
    CompetitorMetric,
    CompetitorSerpObservation,
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
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.records import save_scan_records
from rank_rent.services.scanner import ScanCancelled, ScanPipeline
from rank_rent.services.us_geography import USGeographyError, USGeographyIndex
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


def canonical_market() -> Market:
    index = USGeographyIndex(Path(__file__).parents[2] / "data" / "us_geography.sqlite3")
    return market_from_geography_record(index.search("St. Louis MO", limit=1)[0].record)


def test_preliminary_scan_does_not_clear_existing_full_score(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()

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


def test_unusable_full_scan_does_not_replace_ranked_score(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "full")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()

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

        assert result["assessment_type"] == "full"
        assert result["score"].evidence_status == "unusable"
        assert opportunity.status == "unusable_review"
        assert opportunity.latest_score == 77.0
        assert opportunity.confidence == "high"
        assert opportunity.score_version == "v1"


def test_unresolved_live_market_fails_before_creating_scan(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis_mo", display_name="St. Louis, MO")

    with Session() as session:
        with pytest.raises(USGeographyError, match="not linked"):
            asyncio.run(
                ScanPipeline(
                    session,
                    research_provider=MinimalResearchProvider(),
                    domain_provider=StaticDomainProvider(),
                    data_mode="live",
                ).run(service, market, source="manual")
            )

        assert session.scalar(select(ScanRunORM)) is None


def test_scan_reuses_queued_row_and_writes_typed_records(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()

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
        assert scan.data_mode == "live"
        assert scan.scan_profile == "testing"
        assert scan.progress_stage == "completed"
        assert scan.adapter_names["market_research"] == "minimal-test-provider"
        assert scan.cache_policy_version == "v2"
        assert scan.planned_cost_usd == scan.estimated_cost_usd
        assert scan.scoring_version == "v2.6"
        assert scan.request_parameters["market_payload"]["geography_id"] == "place:2965000"
        assert scan.request_parameters["final_market_payload"]["geography_id"] == "place:2965000"
        assert len(session.scalars(select(ScanPlanCallORM)).all()) == 4
        assert len(session.scalars(select(KeywordClusterORM)).all()) == 1
        assert len(session.scalars(select(KeywordDecisionORM)).all()) >= 2
        assert len(session.scalars(select(KeywordMetricORM)).all()) == 1
        assert len(session.scalars(select(SerpSnapshotORM)).all()) == 1
        assert len(session.scalars(select(ProviderCandidateORM)).all()) == 1


def test_typed_competitor_records_preserve_serp_provenance() -> None:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        scan = ScanRunORM(source="test", status="running")
        session.add(scan)
        session.flush()
        save_scan_records(
            session,
            scan_run_id=scan.id,
            opportunity_id=None,
            metrics=[],
            serp_snapshots=[],
            competitors=[
                CompetitorMetric(
                    url="https://local.example",
                    domain="local.example",
                    referring_domains=25,
                    representative_query="drywall repair st louis",
                    serp_position=1,
                    serp_observations=[
                        CompetitorSerpObservation(
                            query="drywall repair st louis",
                            position=1,
                            url="https://local.example",
                        )
                    ],
                )
            ],
            providers=[],
        )
        session.flush()

        competitor = session.scalar(select(CompetitorMetricORM))
        assert competitor is not None
        assert competitor.representative_query == "drywall repair st louis"
        assert competitor.serp_position == 1
        assert competitor.serp_observations == [
            {
                "query": "drywall repair st louis",
                "position": 1,
                "url": "https://local.example",
            }
        ]


def test_cancelled_queued_scan_does_not_run_provider_calls(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "testing")
    get_settings.cache_clear()
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()

    with Session() as session:
        queued = ScanRunORM(source="manual_async", status="queued", cancel_requested=True)
        session.add(queued)
        session.commit()

        with pytest.raises(ScanCancelled):
            asyncio.run(
                ScanPipeline(
                    session,
                    research_provider=MinimalResearchProvider(),
                    domain_provider=StaticDomainProvider(),
                    data_mode="live",
                ).run(service, market, source="manual_async", existing_scan_id=queued.id)
            )

        scan = session.get(ScanRunORM, queued.id)
        assert scan is not None
        assert scan.status == "cancelled"
        assert scan.progress_stage == "cancelled"
        assert len(session.scalars(select(KeywordMetricORM)).all()) == 0
