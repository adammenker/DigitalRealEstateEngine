from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rank_rent.services.service_catalog import (
    CatalogServiceDefinition,
    ServiceCatalog,
    ServiceCatalogConfig,
    ServiceCatalogError,
    ServiceMatchKind,
)

ROOT = Path(__file__).parents[2]


def _catalog() -> ServiceCatalog:
    return ServiceCatalog.from_path(ROOT / "config/services.yaml")


def test_catalog_loads_versioned_authoritative_services() -> None:
    catalog = _catalog()

    assert catalog.version == "2026.07.1"
    assert {record.service.id for record in catalog.list_services()} >= {
        "drywall_services",
        "epoxy_flooring",
        "plumbing",
        "water_heater_services",
    }

    resolution = catalog.resolve("water_heater_services")
    assert resolution is not None
    assert resolution.configured is True
    assert resolution.match_kind == ServiceMatchKind.id
    assert resolution.service.seed_queries == [
        "water heater repair",
        "water heater replacement",
        "emergency water heater repair",
        "tankless water heater installation",
    ]
    assert resolution.service.intent_modifiers == [
        "repair",
        "replacement",
        "emergency",
        "installation",
    ]
    assert resolution.service.negative_terms == ["diy", "jobs", "salary"]
    assert resolution.service.negative_product_terms == [
        "parts",
        "manual",
        "kit",
        "lowes",
        "home depot",
    ]
    assert resolution.service.provider_categories == [
        "plumber",
        "water heater installation service",
    ]


@pytest.mark.parametrize(
    ("query", "match_kind"),
    [
        ("water_heater_services", ServiceMatchKind.id),
        ("water-heater-services", ServiceMatchKind.slug),
        ("  WATER   HEATER SERVICES ", ServiceMatchKind.display_name),
        ("Hot Water Heater Repair", ServiceMatchKind.alias),
    ],
)
def test_catalog_resolves_each_supported_lookup_kind(
    query: str,
    match_kind: ServiceMatchKind,
) -> None:
    resolution = _catalog().resolve(query)

    assert resolution is not None
    assert resolution.service.id == "water_heater_services"
    assert resolution.match_kind == match_kind
    assert resolution.configured is True


def test_search_is_ranked_and_suitable_for_ui_results() -> None:
    catalog = _catalog()

    exact = catalog.search("garage floor coating")
    partial = catalog.search("heater")

    assert [record.service.id for record in exact] == ["epoxy_flooring"]
    assert exact[0].aliases
    assert exact[0].service.description
    assert [record.service.id for record in partial] == ["water_heater_services"]
    assert catalog.search("", limit=1)[0].service.id == "drywall_services"


def test_unknown_service_requires_an_explicit_unconfigured_draft() -> None:
    catalog = _catalog()

    assert catalog.resolve("Chimney Sweep") is None

    draft = catalog.create_draft("  Chimney   Sweep ")
    assert draft.configured is False
    assert draft.match_kind == ServiceMatchKind.draft
    assert draft.service.id == "draft-chimney-sweep"
    assert draft.service.display_name == "Chimney Sweep"
    assert draft.service.seed_queries == ["Chimney Sweep"]
    assert draft.service.provider_categories == []
    assert draft.service.intent_modifiers == []
    assert draft.service.negative_terms == []
    assert draft.service.negative_product_terms == []


def test_config_rejects_duplicate_ids_and_cross_service_aliases() -> None:
    shared_fields = {
        "description": "",
        "seed_queries": ["example service"],
        "negative_terms": [],
        "intent_modifiers": [],
        "negative_product_terms": [],
        "provider_categories": ["contractor"],
    }

    with pytest.raises(ValidationError, match="Duplicate service id"):
        ServiceCatalogConfig.model_validate(
            {
                "version": "test",
                "services": [
                    {
                        **shared_fields,
                        "id": "example",
                        "slug": "example",
                        "display_name": "Example",
                    },
                    {
                        **shared_fields,
                        "id": "example",
                        "slug": "example-two",
                        "display_name": "Example Two",
                    },
                ],
            }
        )

    with pytest.raises(ValidationError, match="Duplicate service alias or lookup key"):
        ServiceCatalogConfig.model_validate(
            {
                "version": "test",
                "services": [
                    {
                        **shared_fields,
                        "id": "first",
                        "slug": "first",
                        "display_name": "First Service",
                        "aliases": ["Shared Name"],
                    },
                    {
                        **shared_fields,
                        "id": "second",
                        "slug": "second",
                        "display_name": "Shared Name",
                    },
                ],
            }
        )


def test_definition_rejects_malformed_fields_and_unknown_config_keys() -> None:
    with pytest.raises(ValidationError):
        CatalogServiceDefinition.model_validate(
            {
                "id": "Not Stable",
                "slug": "Not A Slug",
                "display_name": "Broken",
                "seed_queries": [],
                "provider_categories": [],
                "unexpected": True,
            }
        )


def test_loader_wraps_yaml_and_schema_failures_with_source_path(tmp_path: Path) -> None:
    malformed_yaml = tmp_path / "malformed.yaml"
    malformed_yaml.write_text("services: [")
    with pytest.raises(ServiceCatalogError, match=str(malformed_yaml)):
        ServiceCatalog.from_path(malformed_yaml)

    invalid_schema = tmp_path / "invalid.yaml"
    invalid_schema.write_text("version: test\nservices: []\n")
    with pytest.raises(ServiceCatalogError, match=str(invalid_schema)):
        ServiceCatalog.from_path(invalid_schema)


def test_disabled_services_are_hidden_by_default() -> None:
    config = ServiceCatalogConfig.model_validate(
        {
            "version": "test",
            "services": [
                {
                    "id": "disabled_service",
                    "slug": "disabled-service",
                    "display_name": "Disabled Service",
                    "aliases": [],
                    "seed_queries": ["disabled service"],
                    "provider_categories": ["contractor"],
                    "enabled": False,
                }
            ],
        }
    )
    catalog = ServiceCatalog(config)

    assert catalog.list_services() == []
    assert catalog.search("disabled") == []
    assert catalog.list_services(include_disabled=True)[0].service.enabled is False
    assert catalog.resolve("disabled_service") is not None
