from __future__ import annotations

import pytest

from rank_rent.services.seeds import SeedValidationError, load_markets, load_services


def test_service_seed_validation_reports_duplicates_and_empty_queries(tmp_path) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(
        """
services:
  - id: drywall
    display_name: Drywall
    seed_queries: []
  - id: drywall
    display_name: Drywall Again
    seed_queries:
      - drywall repair
"""
    )

    with pytest.raises(SeedValidationError) as exc_info:
        load_services(path)

    message = str(exc_info.value)
    assert "services[0].seed_queries" in message
    assert "services[1].id" in message


def test_market_seed_validation_reports_zip_country_and_coordinate_errors(tmp_path) -> None:
    path = tmp_path / "locations.yaml"
    path.write_text(
        """
locations:
  - id: bad_market
    display_name: Bad Market
    country_code: CA
    cities:
      - Toronto
      - Toronto
    postal_codes:
      - ABCDE
      - ABCDE
    center:
      latitude: 120
      longitude: -200
"""
    )

    with pytest.raises(SeedValidationError) as exc_info:
        load_markets(path)

    message = str(exc_info.value)
    assert "locations[0].country_code" in message
    assert "locations[0].cities" in message
    assert "locations[0].postal_codes" in message
    assert "locations[0].center.latitude" in message
    assert "locations[0].center.longitude" in message
