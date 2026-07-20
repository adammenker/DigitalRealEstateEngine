from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from rank_rent.public_data.models import DatasetKind, DatasetRecord, DatasetRelease


@runtime_checkable
class PublicDataAdapter(Protocol):
    """Source boundary used by refresh tooling.

    Network-backed implementations can be added later without changing staging or
    assessment code. Adapters return normalized records and never activate data.
    """

    @property
    def release(self) -> DatasetRelease: ...

    def records(self) -> Iterable[DatasetRecord]: ...


class DatasetSourceAdapter(ABC):
    dataset: ClassVar[DatasetKind]

    @property
    @abstractmethod
    def release(self) -> DatasetRelease:
        """Describe the acquired source release, including its checksum."""

    @abstractmethod
    def records(self) -> Iterable[DatasetRecord]:
        """Yield source records normalized to the shared contract."""


class ACSAdapter(DatasetSourceAdapter):
    dataset = DatasetKind.acs


class CBPAdapter(DatasetSourceAdapter):
    dataset = DatasetKind.cbp


class NESAdapter(DatasetSourceAdapter):
    dataset = DatasetKind.nes


class NOAAAdapter(DatasetSourceAdapter):
    dataset = DatasetKind.noaa


class FEMAAdapter(DatasetSourceAdapter):
    dataset = DatasetKind.fema


class OfflineFixtureAdapter:
    """Reads deterministic JSON/JSONL exports for development and tests."""

    def __init__(self, release: DatasetRelease, source_path: Path) -> None:
        self.source_path = source_path
        if not source_path.is_file():
            raise FileNotFoundError(f"Public-data fixture does not exist: {source_path}")
        self._release = release.model_copy(
            update={
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()
            }
        )

    @property
    def release(self) -> DatasetRelease:
        return self._release

    def records(self) -> Iterable[DatasetRecord]:
        if self.source_path.suffix.lower() == ".jsonl":
            for line_number, line in enumerate(
                self.source_path.read_text().splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON on {self.source_path}:{line_number}."
                    ) from exc
                yield DatasetRecord.model_validate(payload)
            return

        payload = json.loads(self.source_path.read_text())
        records = payload.get("records") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError("Fixture JSON must be a list or an object with a records list.")
        for record in records:
            yield DatasetRecord.model_validate(record)
