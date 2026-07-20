import json
from datetime import date
from pathlib import Path

import pytest

from rank_rent.public_data.adapters import OfflineFixtureAdapter, PublicDataAdapter
from rank_rent.public_data.models import DatasetKind, DatasetRelease
from rank_rent.public_data.store import (
    DatasetNotFoundError,
    DatasetValidationError,
    PublicDataStore,
)

ROOT = Path(__file__).parents[2]


def _release(
    dataset: DatasetKind,
    version: str,
    *,
    release_date: date = date(2024, 6, 27),
) -> DatasetRelease:
    return DatasetRelease(
        dataset=dataset,
        version=version,
        data_year=2022,
        release_date=release_date,
        source_url=f"https://example.test/{dataset.value}/{version}",
        source_name=f"{dataset.value.upper()} fixture",
        license="Public domain test fixture",
        geographic_granularity=["county"],
        refresh_cadence="annual",
        adapter="offline_fixture",
    )


def _adapter(
    dataset: DatasetKind,
    version: str,
    fixture_name: str,
    *,
    release_date: date = date(2024, 6, 27),
) -> OfflineFixtureAdapter:
    return OfflineFixtureAdapter(
        _release(dataset, version, release_date=release_date),
        ROOT / "tests" / "fixtures" / "public_data" / fixture_name,
    )


def test_fixture_adapter_implements_public_data_protocol() -> None:
    adapter = _adapter(DatasetKind.cbp, "v1", "cbp.jsonl")
    assert isinstance(adapter, PublicDataAdapter)
    assert len(list(adapter.records())) == 2


def test_stage_validates_records_and_registers_checksum(tmp_path: Path) -> None:
    store = PublicDataStore(tmp_path / "store")

    staged = store.stage(_adapter(DatasetKind.cbp, "2022-v1", "cbp.jsonl"))

    assert staged.manifest.record_count == 2
    assert len(staged.manifest.content_sha256) == 64
    assert staged.manifest.release.source_sha256 is not None
    assert len(staged.manifest.release.source_sha256) == 64
    assert store.registry().registration(DatasetKind.cbp).active_version is None
    assert store.validate(DatasetKind.cbp, "2022-v1") == staged.manifest


def test_checksum_tampering_blocks_validation_and_activation(tmp_path: Path) -> None:
    store = PublicDataStore(tmp_path / "store")
    staged = store.stage(_adapter(DatasetKind.cbp, "2022-v1", "cbp.jsonl"))
    records_path = staged.path / "records.jsonl"
    records_path.write_text(records_path.read_text() + "{}\n")

    with pytest.raises(DatasetValidationError, match="Checksum mismatch"):
        store.validate(DatasetKind.cbp, "2022-v1")
    with pytest.raises(DatasetValidationError, match="Checksum mismatch"):
        store.activate(DatasetKind.cbp, "2022-v1")


def test_duplicate_record_identity_fails_staging(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.json"
    record = {
        "geography_id": "place:1",
        "source_geoid": "001",
        "geography_level": "county",
        "dimensions": {"naics_code": "238220"},
        "values": {"establishments": 10},
    }
    source.write_text(json.dumps([record, record]))
    store = PublicDataStore(tmp_path / "store")

    with pytest.raises(DatasetValidationError, match="Duplicate public-data record"):
        store.stage(
            OfflineFixtureAdapter(
                _release(DatasetKind.cbp, "duplicate"),
                source,
            )
        )

    assert not store.registry_path.exists()


def test_activation_is_versioned_and_rollback_is_reversible(tmp_path: Path) -> None:
    store = PublicDataStore(tmp_path / "store")
    first = store.stage(_adapter(DatasetKind.cbp, "v1", "cbp.jsonl"))
    store.activate(DatasetKind.cbp, first.version)

    second = store.stage(_adapter(DatasetKind.cbp, "v2", "cbp-v2.jsonl"))
    active = store.activate(DatasetKind.cbp, second.version)

    assert active.release.version == "v2"
    disk_manifest = json.loads((second.path / "manifest.json").read_text())
    assert disk_manifest["status"] == "staged"
    assert disk_manifest["activated_at"] is None
    assert store.active_records(DatasetKind.cbp)[0].values["establishments"] == 55
    rolled_back = store.rollback(DatasetKind.cbp)
    assert rolled_back.release.version == "v1"
    assert store.active_records(DatasetKind.cbp)[0].values["establishments"] == 40


def test_rollback_requires_a_previous_activated_release(tmp_path: Path) -> None:
    store = PublicDataStore(tmp_path / "store")
    store.stage(_adapter(DatasetKind.cbp, "v1", "cbp.jsonl"))
    store.activate(DatasetKind.cbp, "v1")

    with pytest.raises(DatasetNotFoundError, match="No previous activated"):
        store.rollback(DatasetKind.cbp)


def test_snapshot_warns_when_active_release_is_old(tmp_path: Path) -> None:
    store = PublicDataStore(tmp_path / "store")
    store.stage(
        _adapter(
            DatasetKind.cbp,
            "old",
            "cbp.jsonl",
            release_date=date(2020, 1, 1),
        )
    )
    store.activate(DatasetKind.cbp, "old")

    snapshot = store.snapshot(
        "place:2965000",
        county_fips="29510",
        warning_age_days={DatasetKind.cbp: 365},
        as_of=date(2026, 1, 1),
    )

    assert snapshot.records[DatasetKind.cbp]
    assert snapshot.manifests[DatasetKind.cbp].release.source_url.endswith("/cbp/old")
    assert len(snapshot.warnings) == 1
    assert "warning threshold is 365 days" in snapshot.warnings[0]
