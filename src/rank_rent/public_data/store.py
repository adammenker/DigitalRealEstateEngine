from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from rank_rent.public_data.adapters import PublicDataAdapter
from rank_rent.public_data.models import (
    DatasetKind,
    DatasetManifest,
    DatasetRecord,
    DatasetStatus,
    PublicDataRegistry,
    PublicDataSnapshot,
)

RECORDS_FILENAME = "records.jsonl"
MANIFEST_FILENAME = "manifest.json"
REGISTRY_FILENAME = "registry.json"


class DatasetValidationError(ValueError):
    pass


class DatasetNotFoundError(FileNotFoundError):
    pass


@dataclass(frozen=True)
class StagedDataset:
    dataset: DatasetKind
    version: str
    path: Path
    manifest: DatasetManifest


class PublicDataStore:
    """Immutable release store with atomic active-version pointers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.datasets_root = root / "datasets"
        self.registry_path = root / REGISTRY_FILENAME

    def registry(self) -> PublicDataRegistry:
        if not self.registry_path.is_file():
            return PublicDataRegistry()
        try:
            return PublicDataRegistry.model_validate_json(self.registry_path.read_text())
        except (ValueError, OSError) as exc:
            raise DatasetValidationError(
                f"Public-data registry is invalid: {self.registry_path}"
            ) from exc

    def stage(self, adapter: PublicDataAdapter) -> StagedDataset:
        release = adapter.release
        if release.source_sha256 is None:
            raise DatasetValidationError(
                "A source-file SHA-256 checksum is required before staging."
            )
        destination = self._release_path(release.dataset, release.version)
        if destination.exists():
            raise DatasetValidationError(
                f"{release.dataset.value} {release.version} is already staged."
            )
        self.datasets_root.mkdir(parents=True, exist_ok=True)
        staging_root = self.root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f"{release.dataset.value}-{release.version}-",
                dir=staging_root,
            )
        )
        try:
            records_path = temporary / RECORDS_FILENAME
            record_count, content_sha256 = self._write_validated_records(
                records_path,
                adapter.records(),
            )
            manifest = DatasetManifest(
                release=release,
                status=DatasetStatus.staged,
                record_count=record_count,
                content_sha256=content_sha256,
            )
            self._write_json(
                temporary / MANIFEST_FILENAME,
                manifest.model_dump(mode="json"),
            )
            self._validate_release_directory(temporary, expected_manifest=manifest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
            self._register_staged(manifest)
            return StagedDataset(
                dataset=release.dataset,
                version=release.version,
                path=destination,
                manifest=manifest,
            )
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def validate(self, dataset: DatasetKind, version: str) -> DatasetManifest:
        registration = self.registry().registration(dataset)
        manifest = registration.versions.get(version)
        if manifest is None:
            raise DatasetNotFoundError(f"{dataset.value} {version} is not registered.")
        self._validate_release_directory(
            self._release_path(dataset, version),
            expected_manifest=manifest,
        )
        return manifest

    def activate(self, dataset: DatasetKind, version: str) -> DatasetManifest:
        manifest = self.validate(dataset, version)
        registry = self.registry()
        registration = registry.registration(dataset).model_copy(deep=True)
        if registration.active_version == version:
            return registration.versions[version]

        now = datetime.now(UTC)
        previous = registration.active_version
        if previous is not None:
            previous_manifest = registration.versions[previous].model_copy(
                update={"status": DatasetStatus.superseded}
            )
            registration.versions[previous] = previous_manifest
        active_manifest = manifest.model_copy(
            update={"status": DatasetStatus.active, "activated_at": now}
        )
        registration.versions[version] = active_manifest
        registration.active_version = version
        registration.activation_history = [
            *[item for item in registration.activation_history if item != version],
            version,
        ]
        registry.datasets[dataset] = registration
        registry.updated_at = now
        self._write_registry(registry)
        return active_manifest

    def rollback(self, dataset: DatasetKind) -> DatasetManifest:
        registration = self.registry().registration(dataset)
        active = registration.active_version
        candidates = [
            version
            for version in reversed(registration.activation_history)
            if version != active
        ]
        if not candidates:
            raise DatasetNotFoundError(
                f"No previous activated {dataset.value} release is available."
            )
        return self.activate(dataset, candidates[0])

    def active_manifest(self, dataset: DatasetKind) -> DatasetManifest | None:
        registration = self.registry().registration(dataset)
        if registration.active_version is None:
            return None
        return registration.versions[registration.active_version]

    def active_records(
        self,
        dataset: DatasetKind,
        *,
        geography_id: str | None = None,
        source_geoid: str | None = None,
    ) -> list[DatasetRecord]:
        manifest = self.active_manifest(dataset)
        if manifest is None:
            return []
        self.validate(dataset, manifest.release.version)
        records = self._read_records(
            self._release_path(dataset, manifest.release.version) / RECORDS_FILENAME
        )
        return [
            record
            for record in records
            if (geography_id is None or record.geography_id == geography_id)
            and (source_geoid is None or record.source_geoid == source_geoid)
        ]

    def snapshot(
        self,
        geography_id: str,
        *,
        county_fips: str | None = None,
        warning_age_days: dict[DatasetKind, int] | None = None,
        as_of: date | None = None,
    ) -> PublicDataSnapshot:
        records: dict[DatasetKind, list[DatasetRecord]] = {}
        manifests: dict[DatasetKind, DatasetManifest] = {}
        warnings: list[str] = []
        today = as_of or date.today()
        for dataset in DatasetKind:
            manifest = self.active_manifest(dataset)
            if manifest is None:
                continue
            dataset_records = self.active_records(dataset, geography_id=geography_id)
            if dataset in {DatasetKind.cbp, DatasetKind.nes} and county_fips:
                county_records = self.active_records(dataset, source_geoid=county_fips)
                identities = {record.identity for record in dataset_records}
                dataset_records.extend(
                    record for record in county_records if record.identity not in identities
                )
            records[dataset] = dataset_records
            manifests[dataset] = manifest
            threshold = (warning_age_days or {}).get(dataset)
            age_days = (today - manifest.release.release_date).days
            if threshold is not None and age_days > threshold:
                warnings.append(
                    f"{dataset.value.upper()} {manifest.release.version} is "
                    f"{age_days} days past release; warning threshold is {threshold} days."
                )
        return PublicDataSnapshot(
            geography_id=geography_id,
            records=records,
            manifests=manifests,
            warnings=warnings,
        )

    def _register_staged(self, manifest: DatasetManifest) -> None:
        registry = self.registry()
        dataset = manifest.release.dataset
        registration = registry.registration(dataset).model_copy(deep=True)
        registration.versions[manifest.release.version] = manifest
        registry.datasets[dataset] = registration
        registry.updated_at = datetime.now(UTC)
        self._write_registry(registry)

    def _write_validated_records(
        self,
        path: Path,
        records: Iterable[DatasetRecord],
    ) -> tuple[int, str]:
        identities: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
        digest = hashlib.sha256()
        record_count = 0
        with path.open("wb") as output:
            for record in records:
                validated = DatasetRecord.model_validate(record)
                if validated.identity in identities:
                    raise DatasetValidationError(
                        "Duplicate public-data record identity: "
                        f"{validated.geography_id}/{validated.source_geoid}/"
                        f"{dict(validated.dimensions)}"
                    )
                identities.add(validated.identity)
                encoded = (
                    json.dumps(
                        validated.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
                output.write(encoded)
                digest.update(encoded)
                record_count += 1
        if record_count == 0:
            raise DatasetValidationError("A staged dataset cannot be empty.")
        return record_count, digest.hexdigest()

    def _validate_release_directory(
        self,
        path: Path,
        *,
        expected_manifest: DatasetManifest,
    ) -> None:
        records_path = path / RECORDS_FILENAME
        manifest_path = path / MANIFEST_FILENAME
        if not records_path.is_file() or not manifest_path.is_file():
            raise DatasetValidationError(f"Dataset release is incomplete: {path}")
        disk_manifest = DatasetManifest.model_validate_json(manifest_path.read_text())
        if disk_manifest.release.dataset != expected_manifest.release.dataset:
            raise DatasetValidationError("Dataset manifest kind does not match registry.")
        if disk_manifest.release.version != expected_manifest.release.version:
            raise DatasetValidationError("Dataset manifest version does not match registry.")
        digest = hashlib.sha256(records_path.read_bytes()).hexdigest()
        if digest != expected_manifest.content_sha256:
            raise DatasetValidationError(
                f"Checksum mismatch for {expected_manifest.release.dataset.value} "
                f"{expected_manifest.release.version}."
            )
        records = self._read_records(records_path)
        if len(records) != expected_manifest.record_count:
            raise DatasetValidationError("Dataset record count does not match manifest.")

    @staticmethod
    def _read_records(path: Path) -> list[DatasetRecord]:
        records: list[DatasetRecord] = []
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(DatasetRecord.model_validate_json(line))
            except ValueError as exc:
                raise DatasetValidationError(
                    f"Invalid normalized record at {path}:{line_number}."
                ) from exc
        return records

    def _write_registry(self, registry: PublicDataRegistry) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self.registry_path, registry.model_dump(mode="json"))

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _atomic_write_json(path: Path, payload: object) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(payload, output, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _release_path(self, dataset: DatasetKind, version: str) -> Path:
        return self.datasets_root / dataset.value / version
