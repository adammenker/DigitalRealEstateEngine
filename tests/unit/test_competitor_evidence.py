from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import CompetitorMetricORM, ScanRunORM
from rank_rent.domain.models import CompetitorMetric, CompetitorSerpObservation
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.services.records import competitor_metric_from_orm, save_scan_records
from rank_rent.settings import Settings, get_settings

ROOT = Path(__file__).parents[2]


def live_settings() -> Settings:
    return Settings(
        project_root=ROOT,
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="test-user",
        dataforseo_password="test-password",
        dataforseo_environment="sandbox",
    )


def test_legacy_aggregate_fields_map_to_domain_evidence_only() -> None:
    metric = CompetitorMetric(
        url="https://www.example.com/services/repair",
        domain="www.example.com",
        referring_domains=17,
        backlinks=41,
        authority=22.5,
    )

    assert metric.page_url == "https://www.example.com/services/repair"
    assert metric.normalized_domain == "example.com"
    assert metric.domain_referring_domains == 17
    assert metric.domain_backlinks == 41
    assert metric.domain_authority == 22.5
    assert metric.domain_metrics_available is True
    assert metric.page_referring_domains is None
    assert metric.page_backlinks is None
    assert metric.page_authority is None
    assert metric.page_metrics_available is False


@pytest.mark.asyncio
async def test_live_competitor_metrics_deduplicate_domain_calls_and_do_not_invent_page_metrics() -> None:
    provider = DataForSEOLiveProvider(settings=live_settings())
    provider._post = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tasks": [
                    {
                        "result": [
                            {
                                "referring_domains": 120,
                                "backlinks": 450,
                                "rank": 312,
                            }
                        ]
                    }
                ]
            },
            {
                "tasks": [
                    {
                        "result": [
                            {
                                "referring_domains": 8,
                                "backlinks": 19,
                                "rank": 44,
                            }
                        ]
                    }
                ]
            },
        ]
    )

    metrics = await provider.get_competitor_metrics(
        [
            "https://www.example.com/services/repair",
            "https://example.com/another-page",
            "https://local.example.net/water-heaters",
        ]
    )

    assert provider._post.await_count == 2
    assert [call.args[1][0]["target"] for call in provider._post.await_args_list] == [
        "example.com",
        "local.example.net",
    ]
    assert metrics[0].page_url == "https://www.example.com/services/repair"
    assert metrics[0].normalized_domain == "example.com"
    assert metrics[0].domain_referring_domains == 120
    assert metrics[0].domain_backlinks == 450
    assert metrics[0].domain_authority == 312
    assert metrics[0].page_metrics_available is False
    assert metrics[0].page_referring_domains is None
    assert metrics[0].referring_domains == 120


def test_typed_competitor_record_round_trip_preserves_scoped_evidence_and_observations() -> None:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    observed_at = datetime(2026, 7, 18, 14, 30, tzinfo=UTC)
    captured_at = datetime(2026, 7, 18, 14, 31, tzinfo=UTC)

    with Session() as session:
        scan = ScanRunORM(source="focused-test", status="completed")
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
                    url="https://example.com/service",
                    domain="example.com",
                    page_url="https://example.com/service",
                    normalized_domain="example.com",
                    domain_referring_domains=91,
                    domain_backlinks=240,
                    domain_authority=75,
                    page_referring_domains=7,
                    page_backlinks=13,
                    page_authority=28,
                    page_metrics_available=True,
                    domain_metrics_available=True,
                    representative_query="water heater repair",
                    serp_position=2,
                    serp_observations=[
                        CompetitorSerpObservation(
                            query="water heater repair",
                            position=2,
                            url="https://example.com/service",
                            observed_at=observed_at,
                        ),
                        CompetitorSerpObservation(
                            query="water heater installation",
                            position=4,
                            url="https://example.com/service",
                            observed_at=observed_at,
                        ),
                    ],
                    captured_at=captured_at,
                )
            ],
            providers=[],
        )
        session.commit()

        row = session.scalar(select(CompetitorMetricORM))
        assert row is not None
        restored = competitor_metric_from_orm(row)

    assert restored.domain_referring_domains == 91
    assert restored.page_referring_domains == 7
    assert restored.referring_domains == 91
    assert restored.page_metrics_available is True
    assert len(restored.serp_observations) == 2
    assert restored.serp_observations[0].observed_at.replace(tzinfo=UTC) == observed_at
    assert restored.serp_observations[1].query == "water heater installation"


def test_competitor_evidence_migration_is_current_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "competitor-evidence.db"
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")

    inspector = inspect(make_engine(url))
    columns = {
        column["name"] for column in inspector.get_columns("competitor_metrics")
    }
    assert {
        "page_url",
        "normalized_domain",
        "page_referring_domains",
        "page_backlinks",
        "page_authority",
        "domain_referring_domains",
        "domain_backlinks",
        "domain_authority",
        "page_metrics_available",
        "domain_metrics_available",
        "serp_observation_records",
    } <= columns
