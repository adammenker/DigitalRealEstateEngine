from pathlib import Path
from typing import Protocol

from rank_rent.domain.models import (
    CompetitorMetric,
    DeploymentResult,
    DomainAvailabilityResult,
    KeywordCandidate,
    KeywordMetric,
    Market,
    ProviderCandidate,
    ResolvedLocation,
    SerpSnapshot,
    ServiceFamily,
)


class MarketResearchProvider(Protocol):
    async def resolve_location(self, query: str) -> ResolvedLocation: ...

    async def discover_keywords(
        self, service: ServiceFamily, market: Market
    ) -> list[KeywordCandidate]: ...

    async def get_keyword_metrics(self, keywords: list[str], market: Market) -> list[KeywordMetric]: ...

    async def get_serp_snapshot(self, keyword: str, market: Market) -> SerpSnapshot: ...

    async def get_competitor_metrics(self, urls: list[str]) -> list[CompetitorMetric]: ...

    async def find_providers(self, service: ServiceFamily, market: Market) -> list[ProviderCandidate]: ...


class DomainAvailabilityProvider(Protocol):
    async def check(self, domain: str) -> DomainAvailabilityResult: ...


class DeploymentProvider(Protocol):
    async def deploy_staging(self, build_directory: Path, project_slug: str) -> "DeploymentResult": ...

