from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class NAICSRelationship(StrEnum):
    exact = "exact"
    broad_parent = "broad_parent"
    adjacent = "adjacent"


class MappingConfidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


RELATIONSHIP_WEIGHTS = {
    NAICSRelationship.exact: 1.0,
    NAICSRelationship.broad_parent: 0.55,
    NAICSRelationship.adjacent: 0.25,
}
CONFIDENCE_WEIGHTS = {
    MappingConfidence.high: 1.0,
    MappingConfidence.medium: 0.75,
    MappingConfidence.low: 0.5,
}


class NAICSMapping(BaseModel):
    code: str
    relationship: NAICSRelationship
    confidence: MappingConfidence
    title: str
    notes: str

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) not in {2, 3, 4, 5, 6} or not cleaned.isdigit():
            raise ValueError("NAICS codes must contain 2-6 digits.")
        return cleaned

    @field_validator("title", "notes")
    @classmethod
    def require_review_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("NAICS mappings require a title and review note.")
        return cleaned

    @property
    def evidence_weight(self) -> float:
        return RELATIONSHIP_WEIGHTS[self.relationship] * CONFIDENCE_WEIGHTS[self.confidence]


class ServiceNAICSMapping(BaseModel):
    service_family_id: str
    reviewed_by: str
    reviewed_at: date
    naics_version: str
    mappings: list[NAICSMapping]

    @field_validator("mappings")
    @classmethod
    def unique_codes(cls, value: list[NAICSMapping]) -> list[NAICSMapping]:
        codes = [item.code for item in value]
        if len(codes) != len(set(codes)):
            raise ValueError("A service cannot repeat a NAICS code.")
        if not value:
            raise ValueError("A reviewed service mapping cannot be empty.")
        return value


class NAICSRegistry(BaseModel):
    registry_version: str
    cbp_dataset_year: int = Field(ge=1900, le=2200)
    cbp_release_date: date
    nes_dataset_year: int = Field(ge=1900, le=2200)
    nes_release_date: date
    services: list[ServiceNAICSMapping]

    @model_validator(mode="after")
    def unique_services(self) -> NAICSRegistry:
        service_ids = [item.service_family_id for item in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("The NAICS registry contains duplicate service mappings.")
        return self

    def for_service(self, service_family_id: str) -> ServiceNAICSMapping | None:
        return next(
            (
                item
                for item in self.services
                if item.service_family_id == service_family_id
            ),
            None,
        )


def load_naics_registry(path: Path) -> NAICSRegistry:
    try:
        return NAICSRegistry.model_validate(yaml.safe_load(path.read_text()))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid reviewed NAICS registry: {path}") from exc
