from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from rank_rent.domain.models import ServiceFamily, slugify

DEFAULT_SERVICE_CATALOG_PATH = Path("config/services.yaml")
STABLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_service_text(value: str) -> str:
    """Normalize human-entered service text without changing its meaning."""
    return " ".join(value.strip().casefold().split())


def _search_text(value: str) -> str:
    return normalize_service_text(value.replace("_", " ").replace("-", " "))


def _validated_string_list(values: list[str], field_name: str) -> list[str]:
    normalized_values = [" ".join(value.split()) for value in values]
    if any(not value for value in normalized_values):
        raise ValueError(f"{field_name} cannot contain blank values.")
    comparable = [normalize_service_text(value) for value in normalized_values]
    if len(comparable) != len(set(comparable)):
        raise ValueError(f"{field_name} cannot contain duplicate values.")
    return normalized_values


class ServiceCatalogError(ValueError):
    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"Invalid service catalog {path}: {message}")
        self.path = path


class ServiceMatchKind(StrEnum):
    id = "id"
    slug = "slug"
    display_name = "display_name"
    alias = "alias"
    draft = "draft"


class CatalogServiceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    seed_queries: list[str] = Field(min_length=1)
    negative_terms: list[str] = Field(default_factory=list)
    intent_modifiers: list[str] = Field(default_factory=list)
    negative_product_terms: list[str] = Field(default_factory=list)
    provider_categories: list[str] = Field(min_length=1)
    regulated: bool = False
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not STABLE_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "id must start with a lowercase letter and contain only lowercase "
                "letters, numbers, underscores, or hyphens."
            )
        return value

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must be lowercase kebab-case.")
        return value

    @field_validator(
        "aliases",
        "seed_queries",
        "negative_terms",
        "intent_modifiers",
        "negative_product_terms",
        "provider_categories",
    )
    @classmethod
    def validate_string_list(cls, values: list[str], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "list")
        return _validated_string_list(values, str(field_name))

    def to_service_family(self) -> ServiceFamily:
        return ServiceFamily(
            id=self.id,
            slug=self.slug,
            display_name=self.display_name,
            description=self.description,
            seed_queries=list(self.seed_queries),
            negative_terms=list(self.negative_terms),
            intent_modifiers=list(self.intent_modifiers),
            negative_product_terms=list(self.negative_product_terms),
            provider_categories=list(self.provider_categories),
            regulated=self.regulated,
            enabled=self.enabled,
        )


class ServiceCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: str = Field(min_length=1)
    services: list[CatalogServiceDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_lookup_keys(self) -> Self:
        seen_ids: dict[str, str] = {}
        seen_lookup_keys: dict[str, str] = {}

        for service in self.services:
            normalized_id = service.id.casefold()
            if normalized_id in seen_ids:
                raise ValueError(f"Duplicate service id: {service.id}.")
            seen_ids[normalized_id] = service.id

            lookup_values = [
                service.id,
                service.slug,
                service.display_name,
                *service.aliases,
            ]
            for value in lookup_values:
                key = _search_text(value)
                owner = seen_lookup_keys.get(key)
                if owner is not None and owner != service.id:
                    raise ValueError(
                        f"Duplicate service alias or lookup key '{value}' is shared by "
                        f"{owner} and {service.id}."
                    )
                seen_lookup_keys[key] = service.id
        return self


class CatalogServiceRecord(BaseModel):
    catalog_version: str
    configured: bool = True
    aliases: list[str] = Field(default_factory=list)
    service: ServiceFamily


class ServiceResolution(BaseModel):
    catalog_version: str
    configured: bool
    match_kind: ServiceMatchKind
    query: str
    service: ServiceFamily


class ServiceCatalog:
    def __init__(self, config: ServiceCatalogConfig, source_path: Path | None = None) -> None:
        self.config = config
        self.source_path = source_path
        self._definitions = {service.id: service for service in config.services}
        self._id_index = {service.id.casefold(): service for service in config.services}
        self._slug_index = {service.slug.casefold(): service for service in config.services}
        self._display_index = {
            normalize_service_text(service.display_name): service for service in config.services
        }
        self._alias_index = {
            normalize_service_text(alias): service
            for service in config.services
            for alias in service.aliases
        }

    @classmethod
    def from_path(cls, path: Path = DEFAULT_SERVICE_CATALOG_PATH) -> ServiceCatalog:
        try:
            raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ServiceCatalogError(path, str(exc)) from exc
        except yaml.YAMLError as exc:
            raise ServiceCatalogError(path, f"malformed YAML: {exc}") from exc

        if raw_data is None:
            raise ServiceCatalogError(path, "catalog is empty.")
        try:
            config = ServiceCatalogConfig.model_validate(raw_data)
        except ValidationError as exc:
            raise ServiceCatalogError(path, str(exc)) from exc
        return cls(config, source_path=path)

    @property
    def version(self) -> str:
        return self.config.version

    def list_services(self, *, include_disabled: bool = False) -> list[CatalogServiceRecord]:
        definitions = (
            service
            for service in self.config.services
            if include_disabled or service.enabled
        )
        return [
            self._record(service)
            for service in sorted(
                definitions,
                key=lambda item: (normalize_service_text(item.display_name), item.id),
            )
        ]

    def search(
        self,
        query: str = "",
        *,
        limit: int = 20,
        include_disabled: bool = False,
    ) -> list[CatalogServiceRecord]:
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        normalized_query = _search_text(query)
        if not normalized_query:
            return self.list_services(include_disabled=include_disabled)[:limit]

        query_tokens = set(normalized_query.split())
        matches: list[tuple[int, str, CatalogServiceDefinition]] = []
        for service in self.config.services:
            if not include_disabled and not service.enabled:
                continue
            searchable_values = [
                service.id,
                service.slug,
                service.display_name,
                *service.aliases,
                *service.seed_queries,
            ]
            values = [_search_text(value) for value in searchable_values]
            if normalized_query in values:
                rank = 0
            elif any(value.startswith(normalized_query) for value in values):
                rank = 1
            elif any(query_tokens.issubset(set(value.split())) for value in values):
                rank = 2
            elif any(normalized_query in value for value in values):
                rank = 3
            else:
                continue
            matches.append((rank, normalize_service_text(service.display_name), service))

        matches.sort(key=lambda item: (item[0], item[1], item[2].id))
        return [self._record(service) for _, _, service in matches[:limit]]

    def resolve(self, query: str) -> ServiceResolution | None:
        normalized_query = normalize_service_text(query)
        if not normalized_query:
            return None

        candidates = (
            (self._id_index.get(query.strip().casefold()), ServiceMatchKind.id),
            (self._slug_index.get(query.strip().casefold()), ServiceMatchKind.slug),
            (self._display_index.get(normalized_query), ServiceMatchKind.display_name),
            (self._alias_index.get(normalized_query), ServiceMatchKind.alias),
        )
        for definition, match_kind in candidates:
            if definition is not None:
                return ServiceResolution(
                    catalog_version=self.version,
                    configured=True,
                    match_kind=match_kind,
                    query=" ".join(query.split()),
                    service=definition.to_service_family(),
                )
        return None

    def create_draft(self, service_text: str) -> ServiceResolution:
        display_name = " ".join(service_text.split())
        draft_slug = slugify(display_name)
        if not display_name or not draft_slug:
            raise ValueError("Draft service text must contain letters or numbers.")
        return ServiceResolution(
            catalog_version=self.version,
            configured=False,
            match_kind=ServiceMatchKind.draft,
            query=display_name,
            service=ServiceFamily(
                id=f"draft-{draft_slug}",
                slug=draft_slug,
                display_name=display_name,
                seed_queries=[display_name],
                provider_categories=[],
            ),
        )

    def _record(self, definition: CatalogServiceDefinition) -> CatalogServiceRecord:
        return CatalogServiceRecord(
            catalog_version=self.version,
            aliases=list(definition.aliases),
            service=definition.to_service_family(),
        )


def load_service_catalog(path: Path = DEFAULT_SERVICE_CATALOG_PATH) -> ServiceCatalog:
    return ServiceCatalog.from_path(path)
