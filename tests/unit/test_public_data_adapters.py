import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from rank_rent.public_data.adapters import (
    AcquiredSource,
    ACSAdapter,
    CBPAdapter,
    FEMAAdapter,
    FilePublicDataTransport,
    HttpxPublicDataTransport,
    NESAdapter,
    NOAAAdapter,
    PublicDataAcquisitionError,
    PublicDataNormalizationError,
    SourceRequest,
)
from rank_rent.public_data.models import DatasetKind, DatasetRelease
from rank_rent.public_data.store import PublicDataStore

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "public_data"


class RecordingTransport:
    def __init__(
        self,
        content: bytes,
        *,
        content_type: str = "application/json",
    ) -> None:
        self.content = content
        self.content_type = content_type
        self.requests: list[SourceRequest] = []

    def fetch(self, request: SourceRequest) -> AcquiredSource:
        self.requests.append(request)
        return AcquiredSource(
            content=self.content,
            source_url=request.url,
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
            content_type=self.content_type,
            etag='"fixture-etag"',
            last_modified="Mon, 20 Jul 2026 00:00:00 GMT",
        )


def _release(dataset: DatasetKind, *, year: int = 2023) -> DatasetRelease:
    return DatasetRelease(
        dataset=dataset,
        version=f"{year}-official",
        data_year=year,
        release_date=date(2025, 5, 15),
        source_url="https://www.census.gov/",
        source_name=f"Official Census {dataset.value.upper()}",
        license="U.S. Government public domain",
        geographic_granularity=["county", "place"],
        refresh_cadence="annual",
        adapter="pending_acquisition",
    )


def test_acs_api_acquisition_normalizes_geography_and_housing_measures() -> None:
    source = (FIXTURES / "acs-api.json").read_bytes()
    transport = RecordingTransport(source)
    adapter = ACSAdapter(
        _release(DatasetKind.acs),
        transport=transport,
        api_key="secret-test-key",
    )

    release = adapter.release
    records = list(adapter.records())

    assert len(transport.requests) == 1
    assert transport.requests[0].url.endswith("/2023/acs/acs5")
    assert transport.requests[0].params["for"] == "place:*"
    assert transport.requests[0].params["key"] == "secret-test-key"
    assert records[0].geography_id == "place:2965000"
    assert records[0].values["households"] == 144_891
    assert records[0].values["owner_occupied_units"] == 65_612
    assert records[1].geography_id == "place:2901234"
    assert records[1].values["median_year_built"] is None
    assert release.source_sha256 == hashlib.sha256(source).hexdigest()
    assert release.source_bytes == len(source)
    assert release.source_format == "census_api_json"
    assert release.source_etag == '"fixture-etag"'
    assert release.adapter == "census_acs5_v1"


def test_cbp_api_acquisition_normalizes_naics_and_suppressed_values() -> None:
    transport = RecordingTransport((FIXTURES / "cbp-api.json").read_bytes())
    adapter = CBPAdapter(_release(DatasetKind.cbp), transport=transport)

    records = list(adapter.records())

    assert transport.requests[0].params["LFO"] == "001"
    assert transport.requests[0].params["EMPSZES"] == "001"
    assert records[0].geography_id == "county:29510"
    assert records[0].dimensions == {"naics_code": "238220"}
    assert records[0].values == {
        "establishments": 40,
        "employees": 315,
        "annual_payroll_thousands": 19_420,
    }
    assert records[1].values["employees"] is None


def test_cbp_official_download_aliases_are_supported_without_network() -> None:
    adapter = CBPAdapter(
        _release(DatasetKind.cbp),
        transport=FilePublicDataTransport(FIXTURES / "cbp-download.csv"),
    )

    records = list(adapter.records())

    assert records[0].source_geoid == "29510"
    assert records[0].values["establishments"] == 40
    assert adapter.release.acquisition_method == "offline_file"
    assert adapter.release.source_format == "csv"


def test_nes_official_download_normalizes_establishments_and_receipts() -> None:
    adapter = NESAdapter(
        _release(DatasetKind.nes),
        transport=FilePublicDataTransport(FIXTURES / "nes-download.csv"),
    )

    records = list(adapter.records())

    assert records[0].geography_id == "county:29510"
    assert records[0].dimensions["naics_code"] == "238220"
    assert records[0].values == {
        "nonemployer_businesses": 80,
        "receipts_thousands": 12_450,
    }


def test_official_adapter_stages_with_complete_source_provenance(tmp_path: Path) -> None:
    source = FIXTURES / "nes-download.csv"
    adapter = NESAdapter(
        _release(DatasetKind.nes),
        transport=FilePublicDataTransport(source),
    )

    staged = PublicDataStore(tmp_path / "store").stage(adapter)

    assert staged.manifest.record_count == 2
    assert staged.manifest.release.source_sha256 == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert staged.manifest.release.source_bytes == source.stat().st_size
    assert staged.manifest.release.acquisition_method == "offline_file"
    assert len(staged.manifest.content_sha256) == 64


def test_expected_source_checksum_is_enforced_before_normalization() -> None:
    adapter = ACSAdapter(
        _release(DatasetKind.acs),
        transport=RecordingTransport((FIXTURES / "acs-api.json").read_bytes()),
        expected_sha256="0" * 64,
    )

    with pytest.raises(PublicDataAcquisitionError, match="checksum mismatch"):
        _ = adapter.release


def test_http_transport_uses_injected_client_and_redacts_api_key_from_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_DATA_NETWORK_DISABLED", raising=False)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "private-key"
        return httpx.Response(
            200,
            content=b'[["NAME"], ["ok"]]',
            headers={"content-type": "application/json", "etag": '"abc"'},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        source = HttpxPublicDataTransport(client).fetch(
            SourceRequest(
                "https://api.census.gov/data/2023/test",
                {"get": "NAME", "key": "private-key"},
            )
        )

    assert "private-key" not in source.source_url
    assert "key=" not in source.source_url
    assert "get=NAME" in source.source_url
    assert source.etag == '"abc"'


def test_ci_network_guard_fails_before_http_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_DATA_NETWORK_DISABLED", "true")

    with pytest.raises(PublicDataAcquisitionError, match="disabled"):
        HttpxPublicDataTransport().fetch(
            SourceRequest("https://api.census.gov/data/2023/test", {})
        )


def test_public_data_transport_rejects_unsafe_redirect_before_following() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://127.0.0.1/private"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(PublicDataAcquisitionError, match="safety validation"):
            HttpxPublicDataTransport(client).fetch(
                SourceRequest("https://api.census.gov/data/2023/test", {})
            )

    assert len(requests) == 1


@pytest.mark.parametrize(
    "content, message",
    [
        (b"not-json", "not valid JSON"),
        (b'[["NAME", "state"], ["broken"]]', "header width"),
        (b'[["NAME", "B01003_001E"], ["Somewhere", "10"]]', "geography"),
    ],
)
def test_malformed_census_api_input_fails_closed(
    content: bytes,
    message: str,
) -> None:
    adapter = ACSAdapter(
        _release(DatasetKind.acs),
        transport=RecordingTransport(content),
        source_format="json",
        measures={"B01003_001E": "population"},
    )

    with pytest.raises(PublicDataNormalizationError, match=message):
        list(adapter.records())


def test_malformed_census_csv_row_fails_closed() -> None:
    adapter = NESAdapter(
        _release(DatasetKind.nes),
        transport=RecordingTransport(
            b"STATE,COUNTY,NAICS2022,NESTAB,NRCPTOT\n29,510,238220,80,10,extra\n",
            content_type="text/csv",
        ),
    )

    with pytest.raises(PublicDataNormalizationError, match="more values"):
        list(adapter.records())


def test_optional_noaa_and_fema_adapters_remain_typed_extension_points() -> None:
    assert NOAAAdapter.dataset is DatasetKind.noaa
    assert FEMAAdapter.dataset is DatasetKind.fema
    with pytest.raises(TypeError):
        NOAAAdapter()
    with pytest.raises(TypeError):
        FEMAAdapter()


def test_offline_official_source_cli_refreshes_without_network(tmp_path: Path) -> None:
    store = tmp_path / "store"
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "NO_PROXY": "",
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/public_data.py",
            "--store",
            str(store),
            "refresh",
            "--dataset",
            "cbp",
            "--version",
            "2023-offline",
            "--data-year",
            "2023",
            "--release-date",
            "2025-06-26",
            "--official-source",
            str(FIXTURES / "cbp-download.csv"),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["operation"] == "refresh"
    assert payload["record_count"] == 2
    assert PublicDataStore(store).active_manifest(DatasetKind.cbp) is not None
