from datetime import date
from pathlib import Path

import httpx
import pytest

from rank_rent.domain.models import ServiceFamily
from rank_rent.public_data.adapters import OfflineFixtureAdapter
from rank_rent.public_data.models import DatasetKind, DatasetRelease
from rank_rent.public_data.store import PublicDataStore
from rank_rent.services.market_prefilter import (
    AddressableMarketAssessment,
    AddressableMarketPrefilter,
    MarketPrefilter,
    MarketPrefilterAssessment,
)
from rank_rent.services.service_catalog import load_service_catalog
from rank_rent.services.us_geography import USGeographyIndex

ROOT = Path(__file__).parents[2]


def _prefilter(store: PublicDataStore | None = None) -> AddressableMarketPrefilter:
    return AddressableMarketPrefilter(
        USGeographyIndex(ROOT / "data/us_geography.sqlite3"),
        ROOT / "config/market_prefilter.yaml",
        public_data_store=store,
    )


def _release(
    dataset: DatasetKind,
    version: str,
    *,
    release_date: date = date(2024, 12, 12),
) -> DatasetRelease:
    return DatasetRelease(
        dataset=dataset,
        version=version,
        data_year=2022,
        release_date=release_date,
        source_url=f"https://example.test/{dataset.value}",
        source_name=f"Offline {dataset.value.upper()} fixture",
        license="Public domain test fixture",
        geographic_granularity=["county"],
        refresh_cadence="annual",
        adapter="offline_fixture",
    )


def _activate_fixture(
    store: PublicDataStore,
    dataset: DatasetKind,
    fixture_name: str,
) -> None:
    version = f"{dataset.value}-fixture-v1"
    store.stage(
        OfflineFixtureAdapter(
            _release(dataset, version),
            ROOT / "tests" / "fixtures" / "public_data" / fixture_name,
        )
    )
    store.activate(dataset, version)


def test_compatibility_aliases_point_to_addressable_market_contract() -> None:
    assert MarketPrefilter is AddressableMarketPrefilter
    assert MarketPrefilterAssessment is AddressableMarketAssessment


def test_addressable_assessment_uses_embedded_acs_with_exact_evidence() -> None:
    prefilter = _prefilter()
    service = ServiceFamily(
        id="water_heater_services",
        display_name="Water Heater Services",
    )
    record = prefilter.index.get("place:2965000")
    assert record is not None

    assessment = prefilter.assess_record(service, record)

    assert assessment.assessment_type == "addressable_market"
    assert assessment.assessment_version == "addressable-market-v2.0"
    assert assessment.geography_dataset_version == "us-geography-2024.2"
    assert assessment.service_family_id == "water_heater_services"
    assert assessment.service_profile == "home_services"
    assert assessment.profile_version == 2
    assert assessment.score_available is True
    assert assessment.input_signals["households"] == 144_891
    assert assessment.input_signals["housing_units"] == 174_111
    assert assessment.input_signals["owner_occupied_units"] == 65_612
    assert assessment.input_signals["median_year_built"] == 1938
    assert assessment.dataset_versions["acs"] == "us-geography-2024.2"
    assert any(item.source_measure == "B25001_001E" for item in assessment.evidence)
    assert "SEO-opportunity scoring" in assessment.explanation
    assert assessment.provider_density.combined_supply_density is None
    assert "combined_supply_density" in assessment.missing_signals


def test_all_configured_services_have_distinct_versioned_profiles() -> None:
    prefilter = _prefilter()
    catalog = load_service_catalog(ROOT / "config/services.yaml")
    configured = {
        record.service.id: record.service
        for record in catalog.list_services()
    }

    assert set(configured) <= set(prefilter.config.profiles)
    signatures = {
        service_id: tuple(
            sorted(
                (name, signal.weight)
                for name, signal in prefilter.config.profiles[service_id].signals.items()
            )
        )
        for service_id in configured
    }
    assert len(set(signatures.values())) == len(configured)
    assert all(
        prefilter.config.profiles[service_id].profile_version >= 2
        for service_id in configured
    )


def test_provider_density_uses_reviewed_naics_discounts(
    tmp_path: Path,
) -> None:
    store = PublicDataStore(tmp_path / "public-data")
    _activate_fixture(store, DatasetKind.cbp, "cbp.jsonl")
    _activate_fixture(store, DatasetKind.nes, "nes.jsonl")
    prefilter = _prefilter(store)
    record = prefilter.index.get("place:2965000")
    assert record is not None

    assessment = prefilter.assess_record(
        ServiceFamily(id="plumbing", display_name="Plumbing"),
        record,
    )
    density = assessment.provider_density

    assert density.employer_establishments_raw == 40
    assert density.nonemployer_businesses_raw == 80
    assert density.employer_establishments_weighted == 16.5
    assert density.nonemployer_businesses_weighted == 33
    assert density.employer_establishments_per_10000 == pytest.approx(2.515)
    assert density.nonemployer_businesses_per_10000 == pytest.approx(5.03)
    assert density.combined_supply_density == pytest.approx(7.545)
    assert density.combined_supply_band == "undersupplied"
    assert density.data_confidence == "medium"
    mapping = density.naics_mappings[0]
    assert mapping["relationship"] == "broad_parent"
    assert mapping["confidence"] == "medium"
    assert mapping["evidence_weight"] == pytest.approx(0.4125)
    assert "not exact provider counts" in density.limitations[0]
    supply_evidence = next(
        item for item in assessment.evidence if item.signal == "combined_supply_density"
    )
    assert supply_evidence.source_version == "cbp:cbp-fixture-v1+nes:nes-fixture-v1"
    assert supply_evidence.release_date is not None


def test_batch_prefilter_ranks_markets_with_zero_network_or_paid_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Addressable-market assessment attempted a network call.")

    monkeypatch.setattr(httpx.Client, "request", fail_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail_network)
    prefilter = _prefilter()
    service = ServiceFamily(id="plumbing", display_name="Plumbing")
    records = prefilter.index.list_markets(
        kind="city",
        states=["MO"],
        minimum_population=10_000,
    )

    batch = prefilter.assess_batch(service, records, limit=5)

    assert batch.zero_cost is True
    assert batch.paid_api_calls == 0
    assert batch.candidate_count > 5
    assert batch.returned_count == 5
    assert [assessment.rank for assessment in batch.assessments] == [1, 2, 3, 4, 5]
    scores = [assessment.score for assessment in batch.assessments]
    assert all(score is not None for score in scores)
    numeric_scores = [float(score) for score in scores if score is not None]
    assert numeric_scores == sorted(numeric_scores, reverse=True)


def test_missing_public_signals_do_not_receive_points() -> None:
    prefilter = _prefilter()
    record = prefilter.index.get("place:2965000")
    assert record is not None

    assessment = prefilter.assess_record(
        ServiceFamily(id="roofing", display_name="Roofing"),
        record,
    )

    evidence = {item.signal: item for item in assessment.evidence}
    assert evidence["storm_exposure"].available is False
    assert evidence["storm_exposure"].points == 0
    assert evidence["detached_housing_share"].available is False
    assert evidence["detached_housing_share"].points == 0
    assert assessment.evidence_coverage == pytest.approx(0.5)
    assert assessment.score == sum(assessment.component_scores.values())


def test_stale_activated_data_is_visible_and_reduces_confidence(
    tmp_path: Path,
) -> None:
    store = PublicDataStore(tmp_path / "public-data")
    old_release = _release(
        DatasetKind.cbp,
        "cbp-old",
        release_date=date(2020, 1, 1),
    )
    store.stage(
        OfflineFixtureAdapter(
            old_release,
            ROOT / "tests" / "fixtures" / "public_data" / "cbp.jsonl",
        )
    )
    store.activate(DatasetKind.cbp, "cbp-old")
    prefilter = _prefilter(store)
    record = prefilter.index.get("place:2965000")
    assert record is not None

    assessment = prefilter.assess_record(
        ServiceFamily(id="plumbing", display_name="Plumbing"),
        record,
    )

    assert assessment.data_age_warnings
    assert "CBP cbp-old" in assessment.data_age_warnings[0]
    assert assessment.confidence == "low"


def test_generic_service_uses_generic_profile() -> None:
    prefilter = _prefilter()
    record = prefilter.index.get("place:2147476")
    assert record is not None

    assessment = prefilter.assess_record(
        ServiceFamily(id="music-lessons", display_name="Music Lessons"),
        record,
    )

    assert assessment.service_family_id == "generic_local_service"
    assert assessment.service_profile == "generic_local_service"
    assert set(assessment.component_scores) == {
        "population",
        "households",
        "housing_units",
        "household_density",
    }
