from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ApiCallORM, RawApiResponseORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.planning import build_scan_plan
from rank_rent.runtime import DataMode
from rank_rent.services.cache import RawResponseCache, raw_response_payload
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.us_geography import USGeographyIndex
from rank_rent.settings import Settings
from rank_rent.storage.blobs import FilesystemBlobStore


class StaticDataForSEOClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.request_count = 0

    async def __aenter__(self) -> StaticDataForSEOClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, _path: str, json: list[dict[str, Any]]) -> httpx.Response:
        assert json
        self.request_count += 1
        return httpx.Response(200, json=self.payload)

    async def get(self, _path: str) -> httpx.Response:
        self.request_count += 1
        return httpx.Response(200, json=self.payload)


def make_session() -> sessionmaker[Session]:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def sandbox_settings(blob_path: Path) -> Settings:
    return Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
        dataforseo_login="unit",
        dataforseo_password="unit",
        dataforseo_environment="sandbox",
        blob_store_path=blob_path,
    )


def production_settings(blob_path: Path) -> Settings:
    return Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
        dataforseo_login="unit",
        dataforseo_password="unit",
        dataforseo_environment="production",
        live_scan_depth="testing",
        max_scan_cost_usd=10,
        blob_store_path=blob_path,
    )


def canonical_market() -> Market:
    index = USGeographyIndex(Path(__file__).parents[2] / "data" / "us_geography.sqlite3")
    return market_from_geography_record(index.search("St. Louis MO", limit=1)[0].record)


def response(task_id: str, marker: str) -> dict[str, Any]:
    return {
        "status_code": 20000,
        "tasks": [
            {
                "id": task_id,
                "status_code": 20000,
                "cost": 0,
                "result": [{"marker": marker}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_force_refresh_stores_changed_version_and_moves_logical_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    SessionFactory = make_session()
    settings = sandbox_settings(tmp_path / "blobs")
    endpoint = "/v3/test/refresh"
    params = {"tasks": [{"keyword": "drywall"}]}
    old_payload = response("old-task", "old")
    new_payload = response("new-task", "new")

    with SessionFactory() as session:
        provider = DataForSEOLiveProvider(
            settings=settings,
            session=session,
            force_refresh=True,
            allow_unplanned_requests=True,
        )
        assert provider.cache is not None
        provider.cache.set(endpoint, params, old_payload, provider_task_id="old-task")
        session.commit()
        client = StaticDataForSEOClient(new_payload)
        monkeypatch.setattr(provider, "_client", lambda: client)

        returned = await provider._post(endpoint, params["tasks"])

        rows = session.query(RawApiResponseORM).order_by(RawApiResponseORM.id).all()
        current = provider.cache.get_row(endpoint, params)
        call = session.query(ApiCallORM).one()

        assert returned == new_payload
        assert client.request_count == 1
        assert len(rows) == 2
        assert rows[0].cache_key == rows[1].cache_key
        assert rows[0].object_key != rows[1].object_key
        assert rows[0].object_key is not None
        assert rows[1].object_key is not None
        assert rows[0].checksum in rows[0].object_key
        assert rows[1].checksum in rows[1].object_key
        assert raw_response_payload(rows[0], provider.cache.blob_store) == old_payload
        assert raw_response_payload(rows[1], provider.cache.blob_store) == new_payload
        assert current is not None and current.id == rows[1].id
        assert call.raw_api_response_id == rows[1].id


@pytest.mark.asyncio
async def test_expired_response_refreshes_to_new_immutable_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    SessionFactory = make_session()
    settings = sandbox_settings(tmp_path / "blobs")
    endpoint = "/v3/test/expired"
    params = {"tasks": [{"keyword": "plumbing"}]}
    new_payload = response("new-task", "fresh")

    with SessionFactory() as session:
        provider = DataForSEOLiveProvider(
            settings=settings,
            session=session,
            allow_unplanned_requests=True,
        )
        assert provider.cache is not None
        provider.cache.set(endpoint, params, response("old-task", "stale"))
        stale = provider.cache.get_row(endpoint, params)
        assert stale is not None
        stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        client = StaticDataForSEOClient(new_payload)
        monkeypatch.setattr(provider, "_client", lambda: client)

        returned = await provider._post(endpoint, params["tasks"])

        rows = session.query(RawApiResponseORM).order_by(RawApiResponseORM.id).all()
        current = provider.cache.get_row(endpoint, params)
        assert returned == new_payload
        assert client.request_count == 1
        assert len(rows) == 2
        assert current is not None and current.id == rows[1].id
        assert rows[0].expires_at is not None
        assert rows[1].object_key != rows[0].object_key


def test_valid_blob_is_shared_cache_hit_for_execution_and_planning(tmp_path: Path) -> None:
    SessionFactory = make_session()
    settings = production_settings(tmp_path / "blobs")
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    first_plan = build_scan_plan(settings, DataMode.live, service, market)
    keyword_call = next(
        call for call in first_plan.planned_calls if call.stage == "keyword_discovery"
    )

    with SessionFactory() as session:
        cache = RawResponseCache(
            session,
            keyword_call.provider,
            "v3",
            blob_store=FilesystemBlobStore(settings.blob_store_path),
            storage_backend="filesystem",
        )
        cache.set(keyword_call.endpoint, keyword_call.request_parameters, response("task", "valid"))
        session.commit()

        lookup = cache.lookup(keyword_call.endpoint, keyword_call.request_parameters)
        planned = build_scan_plan(settings, DataMode.live, service, market, session=session)

    planned_keyword = next(
        call for call in planned.planned_calls if call.stage == "keyword_discovery"
    )
    assert lookup.is_valid
    assert planned_keyword.cache_hit
    assert planned.cached_cost_usd == Decimal("0.012")
    assert planned.estimated_uncached_cost_usd == Decimal("0.024")


def test_corrupt_blob_checksum_is_a_miss_for_execution_and_planning(tmp_path: Path) -> None:
    SessionFactory = make_session()
    settings = production_settings(tmp_path / "blobs")
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    first_plan = build_scan_plan(settings, DataMode.live, service, market)
    keyword_call = next(
        call for call in first_plan.planned_calls if call.stage == "keyword_discovery"
    )
    store = FilesystemBlobStore(settings.blob_store_path)

    with SessionFactory() as session:
        cache = RawResponseCache(
            session,
            keyword_call.provider,
            "v3",
            blob_store=store,
            storage_backend="filesystem",
        )
        cache.set(keyword_call.endpoint, keyword_call.request_parameters, {"tasks": []})
        session.commit()
        row = cache.get_row(keyword_call.endpoint, keyword_call.request_parameters)
        assert row is not None and row.object_key is not None
        blob_path = settings.blob_store_path.joinpath(*row.object_key.split("/"))
        blob_path.write_bytes(b'{"taskx":[]}')

        lookup = cache.lookup(keyword_call.endpoint, keyword_call.request_parameters)
        assert cache.get(keyword_call.endpoint, keyword_call.request_parameters) is None
        planned = build_scan_plan(settings, DataMode.live, service, market, session=session)

    planned_keyword = next(
        call for call in planned.planned_calls if call.stage == "keyword_discovery"
    )
    assert not lookup.is_valid
    assert lookup.invalid_reason == "response_integrity_failure"
    assert not planned_keyword.cache_hit
    assert planned.cached_cost_usd == 0
    assert planned.estimated_uncached_cost_usd == Decimal("0.036")


def test_missing_blob_is_a_miss_for_execution_and_planning(tmp_path: Path) -> None:
    SessionFactory = make_session()
    settings = production_settings(tmp_path / "blobs")
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    first_plan = build_scan_plan(settings, DataMode.live, service, market)
    keyword_call = next(
        call for call in first_plan.planned_calls if call.stage == "keyword_discovery"
    )
    store = FilesystemBlobStore(settings.blob_store_path)

    with SessionFactory() as session:
        cache = RawResponseCache(
            session,
            keyword_call.provider,
            "v3",
            blob_store=store,
            storage_backend="filesystem",
        )
        cache.set(keyword_call.endpoint, keyword_call.request_parameters, {"tasks": []})
        session.commit()
        row = cache.get_row(keyword_call.endpoint, keyword_call.request_parameters)
        assert row is not None and row.object_key is not None
        store.delete(row.object_key)

        lookup = cache.lookup(keyword_call.endpoint, keyword_call.request_parameters)
        planned = build_scan_plan(settings, DataMode.live, service, market, session=session)

    planned_keyword = next(
        call for call in planned.planned_calls if call.stage == "keyword_discovery"
    )
    assert not lookup.is_valid
    assert lookup.invalid_reason == "response_integrity_failure"
    assert not planned_keyword.cache_hit
    assert planned.estimated_uncached_cost_usd == Decimal("0.036")


def test_expired_metadata_is_an_uncached_planning_cost(tmp_path: Path) -> None:
    SessionFactory = make_session()
    settings = production_settings(tmp_path / "blobs")
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    first_plan = build_scan_plan(settings, DataMode.live, service, market)
    keyword_call = next(
        call for call in first_plan.planned_calls if call.stage == "keyword_discovery"
    )

    with SessionFactory() as session:
        cache = RawResponseCache(
            session,
            keyword_call.provider,
            "v3",
            blob_store=FilesystemBlobStore(settings.blob_store_path),
            storage_backend="filesystem",
        )
        cache.set(keyword_call.endpoint, keyword_call.request_parameters, {"tasks": []})
        row = cache.get_row(keyword_call.endpoint, keyword_call.request_parameters)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        lookup = cache.lookup(keyword_call.endpoint, keyword_call.request_parameters)
        planned = build_scan_plan(settings, DataMode.live, service, market, session=session)

    planned_keyword = next(
        call for call in planned.planned_calls if call.stage == "keyword_discovery"
    )
    assert lookup.invalid_reason == "expired"
    assert not planned_keyword.cache_hit
    assert planned.cached_cost_usd == 0
    assert planned.estimated_uncached_cost_usd == Decimal("0.036")
