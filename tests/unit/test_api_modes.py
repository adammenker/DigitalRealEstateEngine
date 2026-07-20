from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, get_session, make_engine
from rank_rent.db.orm import (
    ApiCallORM,
    FullOpportunityScoreORM,
    JsonArtifactORM,
    MarketPrefilterAssessmentORM,
    MarketPrefilterRunORM,
    OpportunityORM,
    PreliminaryAssessmentORM,
    ScanPlanCallORM,
    ScanRunORM,
    ScoreComponentORM,
)
from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    Market,
    ProviderCandidate,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
)
from rank_rent.main import app
from rank_rent.repositories import get_or_create_opportunity, upsert_market, upsert_service
from rank_rent.services.discovery_report import build_api_cost_ledger
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.us_geography import USGeographyIndex
from rank_rent.settings import get_settings


@pytest.fixture(autouse=True)
def fixture_mode_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_MODE", "fixture")
    monkeypatch.setenv("ALLOW_LIVE_API_CALLS", "false")
    monkeypatch.setenv("SCAN_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path / 'api_modes.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.test_sessionmaker = Session

    def override_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    yield
    app.dependency_overrides.clear()
    if hasattr(app.state, "test_sessionmaker"):
        delattr(app.state, "test_sessionmaker")
    get_settings.cache_clear()


def test_meta_exposes_fixture_mode() -> None:
    client = TestClient(app)
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "fixture"
    assert response.json()["synthetic_fixture_data"] is True


def test_service_catalog_api_exposes_authoritative_definitions() -> None:
    response = TestClient(app).get("/api/services", params={"q": "drywall"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_version"] == "2026.07.1"
    assert payload["services"][0]["id"] == "drywall_services"
    assert payload["services"][0]["configured"] is True
    assert payload["services"][0]["provider_categories"] == [
        "drywall contractor"
    ]


def test_full_scan_rejects_unconfigured_draft_service() -> None:
    response = TestClient(app).post(
        "/api/scans",
        json={
            "service_text": "chimney cap polishing",
            "location_text": "Stamford, CT",
            "scan_profile": "full",
            "dry_run": True,
        },
    )

    assert response.status_code == 422
    assert "configured service" in response.json()["detail"]


def test_market_prefilter_is_zero_cost_and_persists_ranked_assessments() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/market-prefilter",
        json={
            "service_text": "water heater repair",
            "states": ["MO"],
            "geography_kind": "city",
            "minimum_population": 10_000,
            "limit": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["zero_cost"] is True
    assert payload["paid_api_calls"] == 0
    assert payload["service"] == "Water Heater Services"
    assert payload["service_resolution"]["configured"] is True
    assert payload["service_profile"] == "home_services"
    assert payload["geography_dataset_version"] == "us-geography-2024.2"
    assert len(payload["assessments"]) == 3
    assert payload["assessments"][0]["rank"] == 1
    assert payload["assessments"][0]["input_signals"]["households"] > 0

    Session = app.state.test_sessionmaker
    with Session() as session:
        assert session.query(MarketPrefilterRunORM).count() == 1
        assert session.query(MarketPrefilterAssessmentORM).count() == 3


def test_market_prefilter_rejects_unknown_state_abbreviations() -> None:
    response = TestClient(app).post(
        "/api/market-prefilter",
        json={
            "service_text": "plumbing",
            "states": ["XX"],
        },
    )

    assert response.status_code == 422
    assert "Unknown U.S. state abbreviations: XX" in response.text


def test_testing_assessment_can_plan_and_queue_full_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_MODE", "live")
    monkeypatch.setenv("ALLOW_LIVE_API_CALLS", "true")
    monkeypatch.setenv("ALLOW_FULL_SCANS", "true")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "test")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "test")
    monkeypatch.setenv("DATAFORSEO_ENVIRONMENT", "sandbox")
    get_settings.cache_clear()
    Session = app.state.test_sessionmaker
    index = USGeographyIndex(
        Path(__file__).parents[2] / "data/us_geography.sqlite3"
    )
    record = index.get("place:2965000")
    assert record is not None
    market = market_from_geography_record(record)
    service = ServiceFamily(
        id="water_heater_services",
        display_name="Water Heater Services",
        seed_queries=[
            "water heater repair",
            "water heater replacement",
            "tankless water heater installation",
        ],
        provider_categories=["plumber"],
    )
    with Session() as session:
        service_row = upsert_service(session, service)
        market_row = upsert_market(session, market)
        opportunity = get_or_create_opportunity(session, service_row, market_row)
        source_scan = ScanRunORM(
            opportunity_id=opportunity.id,
            source="manual_async",
            status="completed",
            data_mode="live",
            scan_profile="testing",
            request_parameters={
                "data_mode": "live",
                "scan_profile": "testing",
                "service_payload": service.model_dump(mode="json"),
                "market_payload": market.model_dump(mode="json"),
                "final_market_payload": market.model_dump(mode="json"),
            },
        )
        session.add(source_scan)
        session.commit()
        opportunity_id = opportunity.id
        source_scan_id = source_scan.id

    client = TestClient(app)
    preview = client.post(
        f"/api/opportunities/{opportunity_id}/promote",
        json={"dry_run": True},
    )

    assert preview.status_code == 200
    assert preview.json()["source_scan_run_id"] == source_scan_id
    assert preview.json()["scan_plan"]["scan_profile"] == "full"
    assert len(preview.json()["scan_plan"]["planned_calls"]) == 13

    queued = client.post(
        f"/api/opportunities/{opportunity_id}/promote",
        json={"dry_run": False, "confirm_live_cost": True},
    )

    assert queued.status_code == 200
    with Session() as session:
        promoted = session.get(ScanRunORM, queued.json()["scan_id"])
        assert promoted is not None
        assert promoted.source == "promotion_async"
        assert promoted.scan_profile == "full"
        assert promoted.source_scan_run_id == source_scan_id
        assert promoted.request_parameters["scan_profile"] == "full"


def test_dry_run_scan_labels_fixture_mode() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/scans",
        json={
            "service_text": "water heater repair",
            "location_text": "Stamford, CT",
            "dry_run": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["data_mode"] == "fixture"
    assert response.json()["synthetic_fixture_data"] is True
    assert (
        response.json()["public_data_prefilter"]["geography_dataset_version"]
        == "us-geography-2024.2"
    )
    assert response.json()["public_data_prefilter"]["input_signals"]["households"] > 0


def test_scan_rejects_ambiguous_unselected_location() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/scans",
        json={
            "service_text": "water heater repair",
            "location_text": "London",
            "country": "US",
            "dry_run": True,
        },
    )

    assert response.status_code == 422
    payload = response.json()["detail"]
    assert "ambiguous" in payload["message"]
    assert any(candidate["label"] == "London, KY, US" for candidate in payload["candidates"])


def test_location_search_returns_offline_geography_options() -> None:
    client = TestClient(app)
    response = client.get("/api/locations/search", params={"q": "Stamford", "country": "US"})

    assert response.status_code == 200
    labels = [item["label"] for item in response.json()["locations"]]
    assert "Stamford, CT, US" in labels


def test_fixture_scan_detail_and_rescore_use_stored_discovery_evidence() -> None:
    client = TestClient(app)
    scan_response = client.post(
        "/api/scans",
        json={
            "service_text": "water heater repair",
            "location_text": "Stamford, CT",
            "dry_run": False,
            "async_run": False,
        },
    )
    assert scan_response.status_code == 200
    opportunity_id = scan_response.json()["opportunity_id"]

    detail_response = client.get(f"/api/opportunities/{opportunity_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    report = next(
        artifact["payload"]
        for artifact in detail["artifacts"]
        if artifact["kind"] == "discovery_report"
    )
    assert report["score_breakdown"]["version"] == "v2.12"
    assert report["summary"]["evidence_status"] == "complete"
    assert report["score_breakdown"]["evidence_status"] == "complete"
    assert report["score_breakdown"]["score_cap"] is None
    assert report["providers"]["provider_count"] > 0
    competitor_trace = report["score_breakdown"]["component_details"][
        "competitor_weakness"
    ]
    assert competitor_trace["maximum_score"] == 22
    assert competitor_trace["formula"].startswith(
        "query_and_position_exposure_weighted_mean(clamp("
    )
    assert competitor_trace["calculation_steps"]
    assert "Median referring domains" in competitor_trace["calculation_steps"][0]["detail"]
    scan_metadata = report["scan_metadata"]
    assert scan_metadata["completed_at"] is not None
    assert scan_metadata["actual_cost_usd"] == 0
    assert scan_metadata["api_cost_ledger"] == {
        "scan_run_id": scan_response.json()["scan_id"],
            "ledger_complete": True,
            "call_count": 0,
            "planned_call_count": 0,
            "executed_call_count": 0,
            "network_call_count": 0,
            "cache_hit_count": 0,
            "failed_call_count": 0,
            "unexecuted_call_count": 0,
            "unexpected_call_count": 0,
            "estimated_cost_usd": 0,
        "actual_cost_usd": 0,
        "calls": [],
    }

    Session = app.state.test_sessionmaker
    with Session() as session:
        component_rows = session.query(ScoreComponentORM).all()
    assert len(component_rows) == 6
    competitor_row = next(
        row for row in component_rows if row.component == "competitor_weakness"
    )
    commercial_row = next(
        row for row in component_rows if row.component == "commercial_value"
    )
    assert competitor_row.formula.startswith(
        "query_and_position_exposure_weighted_mean(clamp("
    )
    assert "average_cpc" not in competitor_row.inputs["measurements"]
    assert "average_cpc" in commercial_row.inputs["measurements"]
    assert competitor_row.inputs["calculation_steps"]
    assert competitor_row.penalties == {}

    rescore_response = client.post(f"/api/opportunities/{opportunity_id}/rescore")
    assert rescore_response.status_code == 200
    assert rescore_response.json()["score"]["scoring_version"] == "v2.12"
    assert rescore_response.json()["discovery_report"]["scan_metadata"]["rescored_from_stored_data"] is True


def test_api_cost_ledger_reconciles_completed_call_rows() -> None:
    Session = app.state.test_sessionmaker
    now = datetime.now(UTC)
    with Session() as session:
        scan = ScanRunORM(source="test", status="completed")
        session.add(scan)
        session.flush()
        session.add_all(
            [
                ScanPlanCallORM(
                    scan_run_id=scan.id,
                    planned_request_id="req-001",
                    provider="dataforseo",
                    endpoint="/v3/keywords_data",
                    stage="keyword_metrics",
                    cache_key="keyword-cost",
                    estimated_cost_usd=0.08,
                ),
                ScanPlanCallORM(
                    scan_run_id=scan.id,
                    planned_request_id="req-002",
                    provider="dataforseo",
                    endpoint="/v3/serp",
                    stage="serp",
                    cache_key="serp-cache",
                    estimated_cost_usd=0.02,
                ),
                ScanPlanCallORM(
                    scan_run_id=scan.id,
                    planned_request_id="req-003",
                    provider="dataforseo",
                    endpoint="/v3/backlinks",
                    stage="competitors",
                    cache_key="backlinks-failed",
                    estimated_cost_usd=0.03,
                ),
                ApiCallORM(
                    scan_run_id=scan.id,
                    planned_request_id="req-001",
                    provider="dataforseo",
                    endpoint="/v3/keywords_data",
                    stage="keyword_metrics",
                    cache_key="keyword-cost",
                    estimated_cost_usd=0.08,
                    actual_cost_usd=0.05,
                    status="completed",
                    started_at=now,
                    completed_at=now,
                ),
                ApiCallORM(
                    scan_run_id=scan.id,
                    planned_request_id="req-002",
                    provider="dataforseo",
                    endpoint="/v3/serp",
                    stage="serp",
                    cache_key="serp-cache",
                    cache_hit=True,
                    estimated_cost_usd=0.02,
                    actual_cost_usd=0,
                    status="cache_hit",
                    started_at=now,
                    completed_at=now,
                ),
                ApiCallORM(
                    scan_run_id=scan.id,
                    planned_request_id="req-003",
                    provider="dataforseo",
                    endpoint="/v3/backlinks",
                    stage="competitors",
                    cache_key="backlinks-failed",
                    estimated_cost_usd=0.03,
                    actual_cost_usd=0,
                    status="failed",
                    error_type="TimeoutError",
                    started_at=now,
                    completed_at=now,
                ),
            ]
        )
        session.commit()
        ledger = build_api_cost_ledger(session, scan.id)

    assert ledger["ledger_complete"] is True
    assert ledger["call_count"] == 3
    assert ledger["planned_call_count"] == 3
    assert ledger["executed_call_count"] == 3
    assert ledger["unexecuted_call_count"] == 0
    assert ledger["unexpected_call_count"] == 0
    assert ledger["network_call_count"] == 2
    assert ledger["cache_hit_count"] == 1
    assert ledger["failed_call_count"] == 1
    assert ledger["estimated_cost_usd"] == 0.13
    assert ledger["actual_cost_usd"] == 0.05
    assert all(call["completed_at"] for call in ledger["calls"])


def test_preliminary_rescore_does_not_overwrite_ranked_opportunity_score() -> None:
    client = TestClient(app)
    Session = app.state.test_sessionmaker
    with Session() as session:
        opportunity_id = _seed_scan_evidence(
            session,
            assessment_type="preliminary",
            scan_profile="testing",
            latest_score=88.0,
            confidence="high",
        )

    response = client.post(f"/api/opportunities/{opportunity_id}/rescore")

    assert response.status_code == 200
    assert response.json()["assessment_type"] == "preliminary"
    with Session() as session:
        opportunity = session.get(OpportunityORM, opportunity_id)
        assert opportunity is not None
        assert opportunity.latest_score == 88.0
        assert opportunity.confidence == "high"
        assert session.query(PreliminaryAssessmentORM).count() == 1
        assert session.query(FullOpportunityScoreORM).count() == 0


def test_full_rescore_updates_ranked_score_and_typed_history() -> None:
    client = TestClient(app)
    Session = app.state.test_sessionmaker
    with Session() as session:
        opportunity_id = _seed_scan_evidence(
            session,
            assessment_type="full",
            scan_profile="full",
            latest_score=None,
            confidence=None,
        )

    response = client.post(
        f"/api/opportunities/{opportunity_id}/rescore",
        json={"reason": "Adopt calibrated evidence-quality thresholds."},
    )

    assert response.status_code == 200
    assert response.json()["assessment_type"] == "full"
    assert response.json()["reason"] == "Adopt calibrated evidence-quality thresholds."
    assert response.json()["diff"]["new_scoring_version"] == "v2.12"
    with Session() as session:
        opportunity = session.get(OpportunityORM, opportunity_id)
        assert opportunity is not None
        assert opportunity.latest_score == response.json()["score"]["total_score"]
        assert opportunity.confidence == response.json()["score"]["confidence"]
        assert session.query(FullOpportunityScoreORM).count() == 1
    history = client.get(f"/api/opportunities/{opportunity_id}").json()[
        "score_history"
    ]
    assert history[0]["reason"] == "Adopt calibrated evidence-quality thresholds."
    assert history[0]["diff"]["new_total_score"] == response.json()["score"]["total_score"]


def test_compare_rejects_preliminary_assessments() -> None:
    client = TestClient(app)
    Session = app.state.test_sessionmaker
    with Session() as session:
        opportunity_id = _seed_scan_evidence(
            session,
            assessment_type="preliminary",
            scan_profile="testing",
            latest_score=None,
            confidence="preliminary",
        )

    response = client.get(
        "/api/opportunities/compare",
        params={"ids": str(opportunity_id)},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["rejected"][0]["opportunity_id"] == opportunity_id


def test_compare_uses_newer_rescore_score_over_older_scan_score() -> None:
    client = TestClient(app)
    Session = app.state.test_sessionmaker
    now = datetime.now(UTC)
    with Session() as session:
        opportunity_id = _seed_empty_opportunity(session)
        session.add_all(
            [
                JsonArtifactORM(
                    opportunity_id=opportunity_id,
                    kind="scan_result",
                    payload={"assessment_type": "full", "score": {"total_score": 10, "scoring_version": "v2"}},
                    created_at=now - timedelta(minutes=5),
                ),
                JsonArtifactORM(
                    opportunity_id=opportunity_id,
                    kind="rescore_result",
                    payload={
                        "assessment_type": "full",
                        "score": {"total_score": 99, "scoring_version": "v2"},
                        "discovery_report": {"summary": {"score": 99}},
                    },
                    created_at=now,
                ),
            ]
        )
        session.commit()

    response = client.get("/api/opportunities/compare", params={"ids": str(opportunity_id)})

    assert response.status_code == 200
    latest = response.json()["opportunities"][0]
    assert latest["latest_score"]["total_score"] == 99
    assert latest["latest_score"]["artifact_kind"] == "rescore_result"
    assert latest["latest_report"]["summary"]["score"] == 99


def _seed_empty_opportunity(session) -> int:
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st-louis-mo", display_name="St. Louis, MO")
    service_row = upsert_service(session, service)
    market_row = upsert_market(session, market)
    opportunity = get_or_create_opportunity(session, service_row, market_row)
    session.flush()
    return int(opportunity.id)


def _seed_scan_evidence(
    session,
    *,
    assessment_type: str,
    scan_profile: str,
    latest_score: float | None,
    confidence: str | None,
) -> int:
    service = ServiceFamily(
        id=f"{assessment_type}-drywall",
        display_name="Drywall",
        seed_queries=["drywall repair"],
        provider_categories=["drywall contractor"],
    )
    market = Market(id=f"{assessment_type}-market", display_name="St. Louis, MO")
    service_row = upsert_service(session, service)
    market_row = upsert_market(session, market)
    opportunity = get_or_create_opportunity(session, service_row, market_row)
    opportunity.latest_score = latest_score
    opportunity.confidence = confidence
    opportunity.score_version = "v2" if latest_score is not None else None
    scan = ScanRunORM(
        opportunity_id=opportunity.id,
        source="manual",
        status="completed",
        data_mode="live",
        scan_profile=scan_profile,
        request_parameters={
            "service_payload": service.model_dump(mode="json"),
            "market_payload": market.model_dump(mode="json"),
            "final_market_payload": market.model_dump(mode="json"),
        },
    )
    session.add(scan)
    session.flush()
    artifact_kind = "preliminary_assessment" if assessment_type == "preliminary" else "scan_result"
    session.add(
        JsonArtifactORM(
            opportunity_id=opportunity.id,
            kind=artifact_kind,
            payload={
                "assessment_type": assessment_type,
                "metrics": [
                    KeywordMetric(
                        keyword="drywall repair",
                        canonical_keyword="drywall repair",
                        intent="commercial",
                        search_volume=100,
                        cpc=12,
                    ).model_dump(mode="json")
                ],
                "serp_snapshots": [
                    SerpSnapshot(
                        query="drywall repair",
                        market_id=market.id,
                        results=[
                            SerpResult(
                                order=1,
                                url="https://local.example",
                                domain="local.example",
                                title="Local Drywall Contractor",
                                classification="local_provider",
                            )
                        ],
                    ).model_dump(mode="json")
                ],
                "competitors": [
                    CompetitorMetric(
                        url=f"https://weak-{index}.example",
                        domain=f"weak-{index}.example",
                        referring_domains=25,
                    ).model_dump(mode="json")
                    for index in range(3)
                ],
                "providers": [
                    ProviderCandidate(
                        name="Local Drywall Co",
                        website="https://local.example",
                        phone="555-0100",
                        business_status="open",
                        suitability_score=85,
                        suitability_signals={
                            "service_fit": {"normalized": 1.0},
                            "geographic_fit": {"normalized": 1.0},
                        },
                    ).model_dump(mode="json")
                ],
            },
        )
    )
    session.commit()
    return int(opportunity.id)
