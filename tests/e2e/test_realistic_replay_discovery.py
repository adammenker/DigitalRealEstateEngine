from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Generator
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from rank_rent.db.base import Base, get_session, make_engine
from rank_rent.db.orm import (
    ApiCallORM,
    CompetitorMetricORM,
    FullOpportunityScoreORM,
    JsonArtifactORM,
    KeywordMetricORM,
    ProviderCandidateORM,
    SerpSnapshotORM,
)
from rank_rent.domain.models import ServiceFamily
from rank_rent.integrations.dataforseo.replay import DataForSEOReplayProvider
from rank_rent.main import app
from rank_rent.replay import load_response_bundle, validate_response_bundle
from rank_rent.runtime import DataMode
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.scanner import ScanPipeline
from rank_rent.services.us_geography import USGeographyIndex
from rank_rent.settings import Settings, get_settings

PROJECT_ROOT = Path(__file__).parents[2]
realistic_response_bundle = cast(
    Callable[[ServiceFamily, Any], dict[str, Any]],
    run_path(
        str(PROJECT_ROOT / "tests/fixtures/realistic_dataforseo_replay.py")
    )["realistic_response_bundle"],
)


def test_realistic_bundle_replays_full_discovery_and_rescores_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_MODE", "replay")
    monkeypatch.setenv("ALLOW_LIVE_API_CALLS", "false")
    monkeypatch.setenv("LIVE_SCAN_DEPTH", "full")
    monkeypatch.setenv("DATAFORSEO_ENVIRONMENT", "production")
    monkeypatch.setenv("SCAN_WORKER_ENABLED", "false")
    monkeypatch.setenv(
        "US_GEOGRAPHY_DATABASE_PATH",
        str(PROJECT_ROOT / "data/us_geography.sqlite3"),
    )
    get_settings.cache_clear()
    settings = Settings(
        data_mode="replay",
        allow_live_api_calls=False,
        live_scan_depth="full",
        dataforseo_environment="production",
        project_root=PROJECT_ROOT,
        us_geography_database_path=PROJECT_ROOT / "data/us_geography.sqlite3",
    )
    service = ServiceFamily(
        id="water_heater_repair",
        display_name="Water Heater Repair",
        seed_queries=[
            "water heater repair",
            "water heater replacement",
            "tankless water heater installation",
        ],
        intent_modifiers=["repair", "replacement", "installation", "emergency"],
        negative_terms=["diy", "jobs", "salary"],
        negative_product_terms=["parts", "manual", "kit", "lowes", "home depot"],
        provider_categories=["plumber", "water heater installation service"],
    )
    geography = USGeographyIndex.from_settings(settings).get("place:2965000")
    assert geography is not None
    market = market_from_geography_record(geography)

    bundle_path = tmp_path / "realistic-dataforseo-bundle.json"
    bundle_path.write_text(
        json.dumps(realistic_response_bundle(service, market), indent=2),
        encoding="utf-8",
    )
    assert validate_response_bundle(str(bundle_path))["response_count"] == 13
    transport = load_response_bundle(str(bundle_path))

    network_attempts = 0

    def fail_if_network_is_opened(*_args: object, **_kwargs: object) -> None:
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("Replay discovery attempted to open an HTTP client.")

    provider = DataForSEOReplayProvider(transport, settings=settings)
    monkeypatch.setattr(provider, "_client", fail_if_network_is_opened)

    database_path = tmp_path / "realistic-replay.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as session:
        result = asyncio.run(
            ScanPipeline(
                session,
                research_provider=provider,
                data_mode=DataMode.replay,
            ).run(service, market, source="e2e:realistic-replay")
        )
        scan_id = result["scan_id"]
        opportunity_id = result["opportunity_id"]
        report_row = session.query(JsonArtifactORM).filter_by(
            opportunity_id=opportunity_id,
            kind="discovery_report",
        ).one()
        report = report_row.payload

        assert result["data_mode"] == "replay"
        assert result["assessment_type"] == "full"
        assert session.query(ApiCallORM).filter_by(scan_run_id=scan_id).count() == 0
        assert session.query(KeywordMetricORM).filter_by(scan_run_id=scan_id).count() == 3
        assert session.query(SerpSnapshotORM).filter_by(scan_run_id=scan_id).count() == 3
        assert session.query(CompetitorMetricORM).filter_by(scan_run_id=scan_id).count() == 5
        assert session.query(ProviderCandidateORM).filter_by(scan_run_id=scan_id).count() == 5
        assert session.query(FullOpportunityScoreORM).filter_by(scan_run_id=scan_id).count() == 1
        assert report["summary"]["service"] == "Water Heater Repair"
        assert report["summary"]["market"] == "St. Louis, MO, US"
        assert report["scan_metadata"]["data_mode"] == "replay"
        assert report["scan_metadata"]["api_cost_ledger"]["network_call_count"] == 0
        assert report["serp_composition"]["classification_counts"]["directory"] == 3
        assert report["serp_composition"]["classification_counts"]["marketplace"] == 3
        assert report["serp_composition"]["classification_counts"]["national_brand"] == 3
        assert report["competitors"]["count"] == 5
        assert report["providers"]["suitable_provider_count"] >= 3

        demand = report["demand"]
        assert demand["raw_volume_granularity"] == "country"
        assert demand["national_service_demand"] == 2650
        assert demand["service_attractiveness_demand"] == 2650
        assert demand["provider_reported_local_demand"] is None
        assert demand["market_demand_kind"] == "estimated_local"
        assert (
            demand["market_estimation_method"]
            == "population_share_from_country_volume"
        )
        assert demand["market_estimation_confidence"] == "low"
        assert 2.0 < demand["estimated_market_demand"] < 2.5

        provider_count_before_rescore = session.query(ProviderCandidateORM).count()
        full_scores_before_rescore = session.query(FullOpportunityScoreORM).count()

    def override_session() -> Generator[Session, None, None]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).post(f"/api/opportunities/{opportunity_id}/rescore")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 200
    rescored = response.json()
    assert rescored["rescored"] is True
    assert rescored["assessment_type"] == "full"
    assert (
        rescored["discovery_report"]["scan_metadata"]["rescored_from_stored_data"]
        is True
    )
    assert network_attempts == 0
    with SessionLocal() as session:
        assert session.query(ApiCallORM).count() == 0
        assert session.query(ProviderCandidateORM).count() == provider_count_before_rescore
        assert session.query(FullOpportunityScoreORM).count() == full_scores_before_rescore + 1
        assert session.query(JsonArtifactORM).filter_by(
            opportunity_id=opportunity_id,
            kind="rescore_result",
        ).count() == 1
