from pathlib import Path

import pytest
from pydantic import ValidationError

from rank_rent.domain.models import ServiceFamily
from rank_rent.services.market_prefilter import MarketPrefilter, MarketPrefilterConfig
from rank_rent.services.us_geography import USGeographyIndex

ROOT = Path(__file__).parents[2]


def _prefilter() -> MarketPrefilter:
    return MarketPrefilter(
        USGeographyIndex(ROOT / "data/us_geography.sqlite3"),
        ROOT / "config/market_prefilter.yaml",
    )


def test_home_service_prefilter_uses_real_acs_housing_signals() -> None:
    prefilter = _prefilter()
    service = ServiceFamily(
        id="water-heater-repair",
        display_name="Water Heater Repair",
    )
    record = prefilter.index.get("place:2965000")
    assert record is not None

    assessment = prefilter.assess_record(service, record)

    assert assessment.assessment_version == "v1"
    assert assessment.geography_dataset_version == "us-geography-2024.2"
    assert assessment.service_profile == "home_services"
    assert assessment.input_signals["households"] == 144_891
    assert assessment.input_signals["housing_units"] == 174_111
    assert assessment.input_signals["owner_occupied_units"] == 65_612
    assert assessment.input_signals["median_year_built"] == 1938
    assert assessment.missing_signals == []
    assert assessment.confidence == "medium"
    assert sum(assessment.component_scores.values()) == assessment.score


def test_prefilter_ranks_many_markets_without_provider_evidence() -> None:
    prefilter = _prefilter()
    service = ServiceFamily(id="plumbing", display_name="Plumbing")

    assessments, candidate_count = prefilter.rank_markets(
        service,
        states=["MO"],
        geography_kind="city",
        minimum_population=10_000,
        limit=5,
    )

    assert candidate_count > 5
    assert len(assessments) == 5
    assert [assessment.rank for assessment in assessments] == [1, 2, 3, 4, 5]
    assert all(
        left.score >= right.score
        for left, right in zip(assessments, assessments[1:], strict=False)
    )
    assert all(assessment.location.state == "MO" for assessment in assessments)
    assert all(
        assessment.input_signals["source"] == "acs_2024_5_year"
        for assessment in assessments
    )


def test_generic_service_uses_generic_market_profile() -> None:
    prefilter = _prefilter()
    record = prefilter.index.get("place:2147476")
    assert record is not None

    assessment = prefilter.assess_record(
        ServiceFamily(id="music-lessons", display_name="Music Lessons"),
        record,
    )

    assert assessment.service_profile == "generic_local_service"
    assert set(assessment.component_scores) == {
        "population",
        "households",
        "housing_units",
        "household_density",
    }


def test_prefilter_config_rejects_unknown_signal_names() -> None:
    with pytest.raises(ValidationError, match="Unsupported prefilter signals: typo"):
        MarketPrefilterConfig.model_validate(
            {
                "version": "test",
                "minimum_population": 1,
                "maximum_results": 10,
                "recommendation_thresholds": {
                    "advance_to_testing": 70,
                    "review": 40,
                },
                "profiles": {
                    "generic_local_service": {
                        "signal_weights": {"typo": 1},
                        "strong_values": {"typo": 100},
                    }
                },
            }
        )
