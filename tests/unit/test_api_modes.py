import pytest
from fastapi.testclient import TestClient

from rank_rent.main import app
from rank_rent.settings import get_settings


@pytest.fixture(autouse=True)
def fixture_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODE", "fixture")
    monkeypatch.setenv("ALLOW_LIVE_API_CALLS", "false")
    get_settings.cache_clear()
    yield
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
