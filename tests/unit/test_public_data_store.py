import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

from rank_rent.public_data.adapters import OfflineFixtureAdapter, PublicDataAdapter
from rank_rent.public_data.models import DatasetKind, DatasetRelease
from rank_rent.public_data.store import (
    DatasetNotFoundError,
    DatasetValidationError,
    PublicDataStore,
    RegistryLockTimeoutError,
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


def test_interrupted_registry_write_is_recovered_without_replacing_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "store"
    store = PublicDataStore(root)
    original_write = store._write_registry
    attempts = 0

    def interrupt_once(registry: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated interruption")
        original_write(registry)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_write_registry", interrupt_once)
    with pytest.raises(OSError, match="simulated interruption"):
        store.stage(_adapter(DatasetKind.cbp, "recoverable", "cbp.jsonl"))

    release_path = root / "datasets" / "cbp" / "recoverable"
    original_manifest = (release_path / "manifest.json").read_bytes()
    recovered = PublicDataStore(root).stage(_adapter(DatasetKind.cbp, "recoverable", "cbp.jsonl"))

    assert (release_path / "manifest.json").read_bytes() == original_manifest
    assert recovered.manifest.release.version == "recoverable"
    assert PublicDataStore(root).validate(DatasetKind.cbp, "recoverable")


def test_concurrent_refreshes_preserve_every_registered_version(tmp_path: Path) -> None:
    root = tmp_path / "store"

    def stage(version: str, fixture: str) -> str:
        result = PublicDataStore(root).stage(_adapter(DatasetKind.cbp, version, fixture))
        return result.version

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = set(
            executor.map(
                lambda args: stage(*args),
                [("concurrent-v1", "cbp.jsonl"), ("concurrent-v2", "cbp-v2.jsonl")],
            )
        )

    registry = PublicDataStore(root).registry()
    assert results == {"concurrent-v1", "concurrent-v2"}
    assert set(registry.registration(DatasetKind.cbp).versions) == results


def test_identical_concurrent_refresh_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "store"

    def stage_identical(_: int) -> str:
        return PublicDataStore(root).stage(
            _adapter(DatasetKind.cbp, "same-version", "cbp.jsonl")
        ).manifest.content_sha256

    with ThreadPoolExecutor(max_workers=2) as executor:
        checksums = list(executor.map(stage_identical, range(2)))

    registration = PublicDataStore(root).registry().registration(DatasetKind.cbp)
    assert checksums[0] == checksums[1]
    assert list(registration.versions) == ["same-version"]


def test_concurrent_activations_are_serialized_and_rollback_remains_valid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    store = PublicDataStore(root)
    store.stage(_adapter(DatasetKind.cbp, "v1", "cbp.jsonl"))
    store.stage(_adapter(DatasetKind.cbp, "v2", "cbp-v2.jsonl"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        activated = list(
            executor.map(
                lambda version: (
                    PublicDataStore(root).activate(DatasetKind.cbp, version).release.version
                ),
                ["v1", "v2"],
            )
        )

    registration = PublicDataStore(root).registry().registration(DatasetKind.cbp)
    assert set(activated) == {"v1", "v2"}
    assert set(registration.activation_history) == {"v1", "v2"}
    assert registration.active_version in {"v1", "v2"}
    rolled_back = PublicDataStore(root).rollback(DatasetKind.cbp)
    assert rolled_back.release.version != registration.active_version


def test_live_owner_lock_times_out_instead_of_being_stolen(tmp_path: Path) -> None:
    root = tmp_path / "store"
    lock = root / ".registry.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps(
            {
                "token": "live",
                "pid": os.getpid(),
                "hostname": __import__("socket").gethostname(),
            }
        )
    )
    old = time.time() - 60
    os.utime(lock, (old, old))
    store = PublicDataStore(
        root,
        lock_timeout_seconds=0.05,
        stale_lock_seconds=0.01,
        lock_poll_seconds=0.005,
    )

    with pytest.raises(RegistryLockTimeoutError, match="Timed out"):
        store.stage(_adapter(DatasetKind.cbp, "blocked", "cbp.jsonl"))


def test_abandoned_stale_lock_is_recovered(tmp_path: Path) -> None:
    root = tmp_path / "store"
    lock = root / ".registry.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps(
            {
                "token": "dead",
                "pid": 2_000_000_000,
                "hostname": __import__("socket").gethostname(),
            }
        )
    )
    old = time.time() - 60
    os.utime(lock, (old, old))
    store = PublicDataStore(
        root,
        lock_timeout_seconds=0.5,
        stale_lock_seconds=0.01,
        lock_poll_seconds=0.005,
    )

    staged = store.stage(_adapter(DatasetKind.cbp, "after-crash", "cbp.jsonl"))

    assert staged.version == "after-crash"
    assert not lock.exists()
