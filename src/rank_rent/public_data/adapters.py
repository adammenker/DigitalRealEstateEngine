from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx

from rank_rent.public_data.models import DatasetKind, DatasetRecord, DatasetRelease


class PublicDataAcquisitionError(ValueError):
    """The authoritative source could not be acquired or validated."""


class PublicDataNormalizationError(ValueError):
    """The acquired source does not match its documented Census format."""


@dataclass(frozen=True)
class SourceRequest:
    url: str
    params: Mapping[str, str]
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class AcquiredSource:
    content: bytes
    source_url: str
    retrieved_at: datetime
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@runtime_checkable
class PublicDataHTTPTransport(Protocol):
    """Injectable acquisition boundary; tests and offline jobs never need a socket."""

    def fetch(self, request: SourceRequest) -> AcquiredSource: ...


class HttpxPublicDataTransport:
    """Production Census downloader with bounded redirects and response validation."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self, request: SourceRequest) -> AcquiredSource:
        if _environment_flag("PUBLIC_DATA_NETWORK_DISABLED"):
            raise PublicDataAcquisitionError(
                "Public-data network acquisition is disabled in this environment."
            )
        if not request.url.startswith("https://"):
            raise PublicDataAcquisitionError("Public-data acquisition requires HTTPS.")
        owns_client = self._client is None
        client = self._client or httpx.Client(follow_redirects=True)
        try:
            response = client.get(
                request.url,
                params=dict(request.params),
                timeout=request.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PublicDataAcquisitionError(
                f"Public-data request failed for {request.url}: {exc}"
            ) from exc
        finally:
            if owns_client:
                client.close()
        if not response.content:
            raise PublicDataAcquisitionError(
                f"Public-data request returned an empty response: {response.url}"
            )
        return AcquiredSource(
            content=response.content,
            source_url=str(response.url.copy_remove_param("key")),
            retrieved_at=datetime.now(UTC),
            content_type=response.headers.get("content-type"),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )


class FilePublicDataTransport:
    """Offline transport for previously downloaded official API or CSV payloads."""

    def __init__(
        self,
        source_path: Path,
        *,
        content_type: str | None = None,
        source_url: str | None = None,
    ) -> None:
        self.source_path = source_path
        self.content_type = content_type
        self.source_url = source_url

    def fetch(self, request: SourceRequest) -> AcquiredSource:
        if not self.source_path.is_file():
            raise PublicDataAcquisitionError(
                f"Public-data source does not exist: {self.source_path}"
            )
        content = self.source_path.read_bytes()
        if not content:
            raise PublicDataAcquisitionError(f"Public-data source is empty: {self.source_path}")
        return AcquiredSource(
            content=content,
            source_url=self.source_url or request.url,
            retrieved_at=datetime.fromtimestamp(
                self.source_path.stat().st_mtime,
                tz=UTC,
            ),
            content_type=self.content_type,
            last_modified=datetime.fromtimestamp(
                self.source_path.stat().st_mtime,
                tz=UTC,
            ).isoformat(),
        )


@runtime_checkable
class PublicDataAdapter(Protocol):
    """Source boundary used by refresh tooling."""

    @property
    def release(self) -> DatasetRelease: ...

    def records(self) -> Iterable[DatasetRecord]: ...


class DatasetSourceAdapter(ABC):
    dataset: ClassVar[DatasetKind]

    @property
    @abstractmethod
    def release(self) -> DatasetRelease:
        """Describe the acquired source release, including its checksum."""

    @abstractmethod
    def records(self) -> Iterable[DatasetRecord]:
        """Yield source records normalized to the shared contract."""


class CensusSourceAdapter(DatasetSourceAdapter):
    """Shared acquisition and parsing for official Census API JSON and CSV exports."""

    adapter_name: ClassVar[str]
    default_endpoint_template: ClassVar[str]

    def __init__(
        self,
        release: DatasetRelease,
        *,
        transport: PublicDataHTTPTransport | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
        expected_sha256: str | None = None,
        source_format: str = "auto",
        timeout_seconds: float = 60.0,
    ) -> None:
        if release.dataset is not self.dataset:
            raise ValueError(f"{type(self).__name__} requires a {self.dataset.value} release.")
        if source_format not in {"auto", "json", "csv"}:
            raise ValueError("source_format must be auto, json, or csv.")
        if expected_sha256 is not None:
            expected_sha256 = _validate_sha256(expected_sha256)
        self._base_release = release
        self._transport = transport or HttpxPublicDataTransport()
        self._endpoint = endpoint or self.default_endpoint_template.format(year=release.data_year)
        self._api_key = api_key
        self._expected_sha256 = expected_sha256 or release.source_sha256
        self._source_format = source_format
        self._timeout_seconds = timeout_seconds
        self._acquired: AcquiredSource | None = None
        self._release: DatasetRelease | None = None

    @property
    def release(self) -> DatasetRelease:
        acquired = self._acquire()
        if self._release is None:
            self._release = self._base_release.model_copy(
                update={
                    "source_url": acquired.source_url,
                    "source_sha256": acquired.sha256,
                    "source_bytes": len(acquired.content),
                    "source_content_type": acquired.content_type,
                    "source_etag": acquired.etag,
                    "source_last_modified": acquired.last_modified,
                    "retrieved_at": acquired.retrieved_at,
                    "adapter": self.adapter_name,
                    "source_format": self._detected_format(acquired),
                    "acquisition_method": (
                        "offline_file"
                        if isinstance(self._transport, FilePublicDataTransport)
                        else "https"
                    ),
                }
            )
        return self._release

    def _request(self) -> SourceRequest:
        params = dict(self.query_parameters())
        if self._api_key:
            params["key"] = self._api_key
        return SourceRequest(
            url=self._endpoint,
            params=params,
            timeout_seconds=self._timeout_seconds,
        )

    @abstractmethod
    def query_parameters(self) -> Mapping[str, str]:
        """Return official Census API query parameters."""

    def _acquire(self) -> AcquiredSource:
        if self._acquired is None:
            acquired = self._transport.fetch(self._request())
            if not acquired.content:
                raise PublicDataAcquisitionError("The acquired public-data source is empty.")
            if self._expected_sha256 and acquired.sha256 != self._expected_sha256:
                raise PublicDataAcquisitionError(
                    "Acquired source checksum mismatch: "
                    f"expected {self._expected_sha256}, got {acquired.sha256}."
                )
            self._acquired = acquired
        return self._acquired

    def _detected_format(self, acquired: AcquiredSource) -> str:
        if self._source_format != "auto":
            return self._source_format
        content_type = (acquired.content_type or "").lower()
        if "json" in content_type or acquired.content.lstrip().startswith(b"["):
            return "census_api_json"
        return "csv"

    def source_rows(self) -> list[dict[str, str]]:
        acquired = self._acquire()
        detected = self._detected_format(acquired)
        if detected in {"json", "census_api_json"}:
            return _parse_census_json(acquired.content)
        return _parse_census_csv(acquired.content)


ACS_MEASURES: dict[str, str] = {
    "B01003_001E": "population",
    "B11001_001E": "households",
    "B25001_001E": "housing_units",
    "B25003_002E": "owner_occupied_units",
    "B25035_001E": "median_year_built",
    "B19013_001E": "median_household_income",
}


class ACSAdapter(CensusSourceAdapter):
    """Normalize ACS 5-year detailed-table API/CSV data."""

    dataset = DatasetKind.acs
    adapter_name = "census_acs5_v1"
    default_endpoint_template = "https://api.census.gov/data/{year}/acs/acs5"

    def __init__(
        self,
        release: DatasetRelease,
        *,
        geography: str = "place:*",
        within: str | None = "state:*",
        measures: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(release, **kwargs)
        self.geography = geography
        self.within = within
        self.measures = dict(measures or ACS_MEASURES)
        if not self.measures:
            raise ValueError("ACS requires at least one requested measure.")

    def query_parameters(self) -> Mapping[str, str]:
        params = {
            "get": ",".join(["NAME", *self.measures]),
            "for": self.geography,
        }
        if self.within:
            params["in"] = self.within
        return params

    def records(self) -> Iterable[DatasetRecord]:
        for row_number, row in enumerate(self.source_rows(), start=2):
            normalized = _uppercase_keys(row)
            geography_level, source_geoid = _census_geography(normalized)
            values = {
                normalized_name: _measurement(
                    normalized.get(source_name),
                    source_name,
                    row_number,
                )
                for source_name, normalized_name in self.measures.items()
            }
            yield DatasetRecord(
                geography_id=f"{geography_level}:{source_geoid}",
                source_geoid=source_geoid,
                geography_level=geography_level,
                values=values,
            )


class _BusinessPatternsAdapter(CensusSourceAdapter):
    naics_variable: str
    measure_aliases: ClassVar[Mapping[str, tuple[str, ...]]]

    def __init__(
        self,
        release: DatasetRelease,
        *,
        naics_variable: str,
        geography: str = "county:*",
        within: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(release, **kwargs)
        self.naics_variable = naics_variable
        self.geography = geography
        self.within = within

    def query_parameters(self) -> Mapping[str, str]:
        requested = ["NAME", self.naics_variable]
        requested.extend(aliases[0] for aliases in self.measure_aliases.values())
        params = {
            "get": ",".join(requested),
            "for": self.geography,
            self.naics_variable: "*",
        }
        if self.within:
            params["in"] = self.within
        return params

    def records(self) -> Iterable[DatasetRecord]:
        for row_number, row in enumerate(self.source_rows(), start=2):
            normalized = _uppercase_keys(row)
            geography_level, source_geoid = _census_geography(normalized)
            naics_code = _first_value(
                normalized,
                (self.naics_variable, "NAICS2022", "NAICS2017", "NAICS"),
            )
            if naics_code is None or not naics_code.strip():
                raise PublicDataNormalizationError(
                    f"Missing NAICS code in source row {row_number}."
                )
            values = {
                measure: _measurement(
                    _first_value(normalized, aliases),
                    aliases[0],
                    row_number,
                )
                for measure, aliases in self.measure_aliases.items()
            }
            yield DatasetRecord(
                geography_id=f"{geography_level}:{source_geoid}",
                source_geoid=source_geoid,
                geography_level=geography_level,
                dimensions={"naics_code": naics_code.strip()},
                values=values,
            )


class CBPAdapter(_BusinessPatternsAdapter):
    """Normalize County Business Patterns API or downloadable CSV records."""

    dataset = DatasetKind.cbp
    adapter_name = "census_cbp_v1"
    default_endpoint_template = "https://api.census.gov/data/{year}/cbp"
    measure_aliases = {
        "establishments": ("ESTAB", "EST"),
        "employees": ("EMP",),
        "annual_payroll_thousands": ("PAYANN", "AP"),
    }

    def __init__(
        self,
        release: DatasetRelease,
        *,
        naics_variable: str = "NAICS2017",
        **kwargs: Any,
    ) -> None:
        super().__init__(release, naics_variable=naics_variable, **kwargs)

    def query_parameters(self) -> Mapping[str, str]:
        return {
            **super().query_parameters(),
            "LFO": "001",
            "EMPSZES": "001",
        }


class NESAdapter(_BusinessPatternsAdapter):
    """Normalize Nonemployer Statistics API or downloadable CSV records."""

    dataset = DatasetKind.nes
    adapter_name = "census_nonemp_v1"
    default_endpoint_template = "https://api.census.gov/data/{year}/nonemp"
    measure_aliases = {
        "nonemployer_businesses": ("NESTAB",),
        "receipts_thousands": ("NRCPTOT", "RCPTOT"),
    }

    def __init__(
        self,
        release: DatasetRelease,
        *,
        naics_variable: str = "NAICS2022",
        **kwargs: Any,
    ) -> None:
        super().__init__(release, naics_variable=naics_variable, **kwargs)


class NOAAAdapter(DatasetSourceAdapter):
    """Typed extension point for optional NOAA reviewed extracts."""

    dataset = DatasetKind.noaa


class FEMAAdapter(DatasetSourceAdapter):
    """Typed extension point for optional FEMA reviewed extracts."""

    dataset = DatasetKind.fema


class OfflineFixtureAdapter:
    """Reads deterministic normalized JSON/JSONL exports for development and tests."""

    def __init__(self, release: DatasetRelease, source_path: Path) -> None:
        self.source_path = source_path
        if not source_path.is_file():
            raise FileNotFoundError(f"Public-data fixture does not exist: {source_path}")
        stat = source_path.stat()
        self._release = release.model_copy(
            update={
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "source_bytes": stat.st_size,
                "source_format": source_path.suffix.lower().lstrip("."),
                "acquisition_method": "offline_normalized_fixture",
                "retrieved_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            }
        )

    @property
    def release(self) -> DatasetRelease:
        return self._release

    def records(self) -> Iterable[DatasetRecord]:
        if self.source_path.suffix.lower() == ".jsonl":
            for line_number, line in enumerate(
                self.source_path.read_text().splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON on {self.source_path}:{line_number}.") from exc
                yield DatasetRecord.model_validate(payload)
            return

        payload = json.loads(self.source_path.read_text())
        records = payload.get("records") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError("Fixture JSON must be a list or an object with a records list.")
        for record in records:
            yield DatasetRecord.model_validate(record)


def _parse_census_json(content: bytes) -> list[dict[str, str]]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicDataNormalizationError("Census API response is not valid JSON.") from exc
    if (
        not isinstance(payload, list)
        or len(payload) < 2
        or not isinstance(payload[0], list)
        or not all(isinstance(header, str) and header.strip() for header in payload[0])
    ):
        raise PublicDataNormalizationError(
            "Census API response must contain a header row and at least one data row."
        )
    headers = payload[0]
    if len(headers) != len(set(headers)):
        raise PublicDataNormalizationError("Census API response contains duplicate headers.")
    records: list[dict[str, str]] = []
    for row_number, row in enumerate(payload[1:], start=2):
        if not isinstance(row, list) or len(row) != len(headers):
            raise PublicDataNormalizationError(
                f"Census API row {row_number} does not match the header width."
            )
        if not all(value is None or isinstance(value, (str, int, float)) for value in row):
            raise PublicDataNormalizationError(
                f"Census API row {row_number} contains a non-scalar value."
            )
        records.append(
            {
                header: "" if value is None else str(value)
                for header, value in zip(headers, row, strict=True)
            }
        )
    return records


def _parse_census_csv(content: bytes) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PublicDataNormalizationError("Census CSV must be UTF-8 encoded.") from exc
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or any(not field.strip() for field in reader.fieldnames):
            raise PublicDataNormalizationError("Census CSV has an invalid header row.")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise PublicDataNormalizationError("Census CSV contains duplicate headers.")
        rows = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise PublicDataNormalizationError(
                    f"Census CSV row {row_number} has more values than the header."
                )
            rows.append({key: value or "" for key, value in row.items()})
    except csv.Error as exc:
        raise PublicDataNormalizationError("Census CSV is malformed.") from exc
    if not rows:
        raise PublicDataNormalizationError("Census CSV contains no data rows.")
    return rows


def _uppercase_keys(row: Mapping[str, str]) -> dict[str, str]:
    return {key.strip().upper(): value.strip() for key, value in row.items()}


def _first_value(row: Mapping[str, str], names: Iterable[str]) -> str | None:
    for name in names:
        value = row.get(name.upper())
        if value is not None:
            return value
    return None


def _census_geography(row: Mapping[str, str]) -> tuple[str, str]:
    normalized = _uppercase_keys(row)
    state = _first_value(normalized, ("STATE", "FIPSTATE", "ST"))
    county = _first_value(normalized, ("COUNTY", "FIPSCTY", "CTY"))
    place = _first_value(normalized, ("PLACE",))
    zcta = _first_value(
        normalized,
        ("ZIP CODE TABULATION AREA", "ZCTA", "ZIPCODE", "ZIP"),
    )
    if place:
        if not state:
            raise PublicDataNormalizationError("Place geography is missing state FIPS.")
        return "place", _fips(state, 2, "state") + _fips(place, 5, "place")
    if county:
        county_clean = county.strip()
        if len(county_clean) == 5 and county_clean.isdigit():
            return "county", county_clean
        if not state:
            geo_id = _first_value(normalized, ("GEO_ID", "GEOID"))
            if geo_id and geo_id[-5:].isdigit():
                return "county", geo_id[-5:]
            raise PublicDataNormalizationError("County geography is missing state FIPS.")
        return "county", _fips(state, 2, "state") + _fips(county, 3, "county")
    if zcta:
        return "zcta", _fips(zcta, 5, "ZCTA")
    geo_id = _first_value(normalized, ("GEO_ID", "GEOID"))
    if geo_id and geo_id[-5:].isdigit():
        return "county", geo_id[-5:]
    raise PublicDataNormalizationError(
        "Source row lacks a supported place, county, or ZCTA geography."
    )


def _fips(value: str, width: int, label: str) -> str:
    cleaned = value.strip()
    if not cleaned.isdigit() or len(cleaned) > width:
        raise PublicDataNormalizationError(f"Invalid {label} FIPS value: {value!r}.")
    return cleaned.zfill(width)


def _measurement(value: str | None, field: str, row_number: int) -> int | float | None:
    if value is None:
        raise PublicDataNormalizationError(
            f"Required field {field} is absent from source row {row_number}."
        )
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned.upper() in {"N", "D", "S", "X", "NA", "NULL", "(X)"}:
        return None
    try:
        number = float(cleaned)
    except ValueError as exc:
        raise PublicDataNormalizationError(
            f"Field {field} has a non-numeric value in source row {row_number}: {value!r}."
        ) from exc
    if number <= -1_000_000:
        return None
    if not math.isfinite(number):
        raise PublicDataNormalizationError(
            f"Field {field} must be finite in source row {row_number}."
        )
    return int(number) if number.is_integer() else number


def _validate_sha256(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) != 64 or any(char not in "0123456789abcdef" for char in cleaned):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 digest.")
    return cleaned


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
