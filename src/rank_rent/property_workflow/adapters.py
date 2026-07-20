from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rank_rent.property_workflow.models import DomainAvailability


@dataclass(frozen=True)
class DomainAvailabilityEvidence:
    status: DomainAvailability
    provider: str
    evidence: dict[str, object]


class DomainAvailabilityAdapter(Protocol):
    async def check(self, domain: str) -> DomainAvailabilityEvidence: ...


class FixtureDomainAvailabilityAdapter:
    """Offline-only domain evidence supplied by a deterministic fixture."""

    def __init__(
        self,
        status: DomainAvailability,
        evidence: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.evidence = evidence or {}

    async def check(self, domain: str) -> DomainAvailabilityEvidence:
        return DomainAvailabilityEvidence(
            status=self.status,
            provider="fixture-offline-v1",
            evidence={"domain": domain, **self.evidence},
        )


class RegistrarAdapter(Protocol):
    can_purchase: bool


class ManualRegistrarAdapter:
    """Marker adapter: registration must happen outside this application."""

    can_purchase = False


@dataclass(frozen=True)
class LocalDeploymentResult:
    url: str
    adapter_name: str
    local_only: bool


class DeploymentAdapter(Protocol):
    public_capable: bool

    async def deploy(
        self,
        source: Path,
        destination: Path,
    ) -> LocalDeploymentResult: ...


class LocalFilesystemDeploymentAdapter:
    public_capable = False

    async def deploy(self, source: Path, destination: Path) -> LocalDeploymentResult:
        if not (source / "index.html").is_file():
            raise RuntimeError("site_build_missing_index")
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        return LocalDeploymentResult(
            url=(destination / "index.html").resolve().as_uri(),
            adapter_name="local-filesystem-v1",
            local_only=True,
        )
