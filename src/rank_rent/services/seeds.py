import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from rank_rent.domain.models import Market, ServiceFamily


class SeedValidationError(ValueError):
    def __init__(self, path: Path, errors: list[str]) -> None:
        super().__init__(f"Invalid seed file {path}: " + "; ".join(errors))
        self.path = path
        self.errors = errors


def _line_hint(item: dict[str, Any], index: int) -> str:
    return f"entry {index + 1} ({item.get('id', 'missing id')})"


def _path(collection: str, index: int, field: str) -> str:
    return f"{collection}[{index}].{field}"


def load_services(path: Path = Path("seeds/services.example.yaml")) -> list[ServiceFamily]:
    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    services: list[ServiceFamily] = []
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for index, raw_item in enumerate(data.get("services", [])):
        item = dict(raw_item)
        try:
            service = ServiceFamily.model_validate(item)
        except ValidationError as exc:
            errors.append(f"{_line_hint(item, index)}: {exc.errors()}")
            continue
        if service.id in seen_ids:
            errors.append(f"{_path('services', index, 'id')}: duplicate id '{service.id}'")
        seen_ids.add(service.id)
        if service.slug in seen_slugs:
            errors.append(f"{_path('services', index, 'slug')}: duplicate slug '{service.slug}'")
        seen_slugs.add(service.slug or service.id)
        if not service.seed_queries:
            errors.append(f"{_path('services', index, 'seed_queries')}: must not be empty")
        services.append(service)
    if errors:
        raise SeedValidationError(path, errors)
    return services


def load_markets(path: Path = Path("seeds/locations.example.yaml")) -> list[Market]:
    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    markets: list[Market] = []
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for index, raw_item in enumerate(data.get("locations", [])):
        item = dict(raw_item)
        if "center" in item:
            center = dict(item.get("center") or {})
            item.pop("center", None)
            item["latitude"] = center.get("latitude")
            item["longitude"] = center.get("longitude")
        try:
            market = Market.model_validate(item)
        except ValidationError as exc:
            errors.append(f"{_line_hint(item, index)}: {exc.errors()}")
            continue
        if market.id in seen_ids:
            errors.append(f"{_path('locations', index, 'id')}: duplicate id '{market.id}'")
        seen_ids.add(market.id)
        if market.slug in seen_slugs:
            errors.append(f"{_path('locations', index, 'slug')}: duplicate slug '{market.slug}'")
        seen_slugs.add(market.slug or market.id)
        if market.country_code != "US":
            errors.append(f"{_path('locations', index, 'country_code')}: only US is supported")
        if len(market.cities) != len(set(market.cities)):
            errors.append(f"{_path('locations', index, 'cities')}: duplicate cities")
        if len(market.postal_codes) != len(set(market.postal_codes)):
            errors.append(f"{_path('locations', index, 'postal_codes')}: duplicate postal codes")
        invalid_zip = [
            code for code in market.postal_codes if not re.fullmatch(r"\d{5}", code)
        ]
        if invalid_zip:
            errors.append(
                f"{_path('locations', index, 'postal_codes')}: malformed ZIP codes {invalid_zip}"
            )
        if market.latitude is not None and not -90 <= market.latitude <= 90:
            errors.append(f"{_path('locations', index, 'center.latitude')}: out of range")
        if market.longitude is not None and not -180 <= market.longitude <= 180:
            errors.append(f"{_path('locations', index, 'center.longitude')}: out of range")
        markets.append(market)
    if errors:
        raise SeedValidationError(path, errors)
    return markets
