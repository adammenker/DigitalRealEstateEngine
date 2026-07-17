import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, get_session, make_engine
from rank_rent.main import app
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

    def override_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_meta_exposes_fixture_mode() -> None:
    client = TestClient(app)
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "fixture"
    assert response.json()["synthetic_fixture_data"] is True


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


def test_location_search_returns_seeded_market_options() -> None:
    client = TestClient(app)
    response = client.get("/api/locations/search", params={"q": "Stamford", "country": "US"})

    assert response.status_code == 200
    labels = [item["label"] for item in response.json()["locations"]]
    assert "Stamford, CT" in labels
