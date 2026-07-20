from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from rank_rent.public_data.models import DatasetKind


class DatasetDefinition(BaseModel):
    dataset: DatasetKind
    enabled: bool = True
    required: bool = False
    source_name: str
    source_url: str
    license: str
    refresh_cadence: str
    geographic_granularity: list[str]
    data_age_warning_days: int = Field(ge=1)
    purpose: str
    limitations: list[str] = Field(default_factory=list)

    @field_validator(
        "source_name",
        "source_url",
        "license",
        "refresh_cadence",
        "purpose",
    )
    @classmethod
    def require_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Dataset catalog metadata cannot be empty.")
        return cleaned


class PublicDatasetCatalog(BaseModel):
    catalog_version: str
    datasets: list[DatasetDefinition]

    @field_validator("datasets")
    @classmethod
    def unique_datasets(
        cls,
        value: list[DatasetDefinition],
    ) -> list[DatasetDefinition]:
        kinds = [item.dataset for item in value]
        if len(kinds) != len(set(kinds)):
            raise ValueError("The public-data catalog contains duplicate datasets.")
        return value

    def definition(self, dataset: DatasetKind) -> DatasetDefinition | None:
        return next((item for item in self.datasets if item.dataset == dataset), None)

    @property
    def warning_age_days(self) -> dict[DatasetKind, int]:
        return {
            item.dataset: item.data_age_warning_days
            for item in self.datasets
            if item.enabled
        }


def load_dataset_catalog(path: Path) -> PublicDatasetCatalog:
    try:
        return PublicDatasetCatalog.model_validate(yaml.safe_load(path.read_text()))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid public-data catalog: {path}") from exc
