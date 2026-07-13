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


def load_services(path: Path = Path("seeds/services.example.yaml")) -> list[ServiceFamily]:
    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    services: list[ServiceFamily] = []
    for index, item in enumerate(data.get("services", [])):
        try:
            services.append(ServiceFamily.model_validate(item))
        except ValidationError as exc:
            errors.append(f"{_line_hint(item, index)}: {exc.errors()}")
    if errors:
        raise SeedValidationError(path, errors)
    return services


def load_markets(path: Path = Path("seeds/locations.example.yaml")) -> list[Market]:
    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    markets: list[Market] = []
    for index, item in enumerate(data.get("locations", [])):
        if "center" in item:
            center = item.pop("center") or {}
            item["latitude"] = center.get("latitude")
            item["longitude"] = center.get("longitude")
        try:
            markets.append(Market.model_validate(item))
        except ValidationError as exc:
            errors.append(f"{_line_hint(item, index)}: {exc.errors()}")
    if errors:
        raise SeedValidationError(path, errors)
    return markets

