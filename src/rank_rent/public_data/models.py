from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class DatasetKind(StrEnum):
    acs = "acs"
    cbp = "cbp"
    nes = "nes"
    noaa = "noaa"
    fema = "fema"


class DatasetStatus(StrEnum):
    staged = "staged"
    active = "active"
    superseded = "superseded"


class DatasetRecord(BaseModel):
    """One normalized public-data observation at a declared geography."""

    geography_id: str
    source_geoid: str
    geography_level: str
    values: dict[str, float | int | None]
    dimensions: dict[str, str] = Field(default_factory=dict)

    @field_validator("geography_id", "source_geoid", "geography_level")
    @classmethod
    def require_nonempty_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Public-data record identifiers cannot be empty.")
        return cleaned

    @field_validator("values")
    @classmethod
    def require_measurements(
        cls,
        value: dict[str, float | int | None],
    ) -> dict[str, float | int | None]:
        if not value:
            raise ValueError("A public-data record must contain at least one value.")
        for measure, measurement in value.items():
            if not measure.strip():
                raise ValueError("Public-data measure names cannot be empty.")
            if isinstance(measurement, float) and (
                measurement != measurement
                or measurement in {float("inf"), float("-inf")}
            ):
                raise ValueError(f"Measure {measure} must be finite.")
        return value

    @property
    def identity(self) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        return (
            self.geography_id,
            self.source_geoid,
            tuple(sorted(self.dimensions.items())),
        )


class DatasetRelease(BaseModel):
    dataset: DatasetKind
    version: str
    data_year: int = Field(ge=1900, le=2200)
    release_date: date
    source_url: str
    source_name: str
    license: str
    source_sha256: str | None = None
    geographic_granularity: list[str]
    refresh_cadence: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    adapter: str
    notes: str = ""

    @field_validator(
        "version",
        "source_url",
        "source_name",
        "license",
        "refresh_cadence",
        "adapter",
    )
    @classmethod
    def require_release_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Dataset release metadata cannot be empty.")
        return cleaned

    @field_validator("geographic_granularity")
    @classmethod
    def require_granularity(cls, value: list[str]) -> list[str]:
        cleaned = sorted({item.strip() for item in value if item.strip()})
        if not cleaned:
            raise ValueError("At least one geographic granularity is required.")
        return cleaned

    @field_validator("source_sha256")
    @classmethod
    def validate_source_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if len(cleaned) != 64 or any(char not in "0123456789abcdef" for char in cleaned):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest.")
        return cleaned


class DatasetManifest(BaseModel):
    schema_version: str = "1"
    release: DatasetRelease
    status: DatasetStatus = DatasetStatus.staged
    record_count: int = Field(ge=0)
    content_sha256: str
    staged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    activated_at: datetime | None = None

    @field_validator("content_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if len(cleaned) != 64 or any(char not in "0123456789abcdef" for char in cleaned):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest.")
        return cleaned


class DatasetRegistration(BaseModel):
    active_version: str | None = None
    activation_history: list[str] = Field(default_factory=list)
    versions: dict[str, DatasetManifest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def active_version_must_exist(self) -> DatasetRegistration:
        if self.active_version is not None and self.active_version not in self.versions:
            raise ValueError("The active dataset version is not registered.")
        unknown_history = set(self.activation_history) - set(self.versions)
        if unknown_history:
            raise ValueError("Activation history contains an unregistered dataset version.")
        return self


class PublicDataRegistry(BaseModel):
    schema_version: str = "1"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    datasets: dict[DatasetKind, DatasetRegistration] = Field(default_factory=dict)

    def registration(self, dataset: DatasetKind) -> DatasetRegistration:
        return self.datasets.get(dataset, DatasetRegistration())


class PublicDataSnapshot(BaseModel):
    """Public evidence assembled for one canonical geography."""

    geography_id: str
    records: dict[DatasetKind, list[DatasetRecord]] = Field(default_factory=dict)
    manifests: dict[DatasetKind, DatasetManifest] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def dataset_values(self, dataset: DatasetKind) -> list[dict[str, Any]]:
        return [
            {
                "source_geoid": record.source_geoid,
                "geography_level": record.geography_level,
                "dimensions": record.dimensions,
                "values": record.values,
            }
            for record in self.records.get(dataset, [])
        ]
