from __future__ import annotations

from rank_rent.domain.interfaces import DomainAvailabilityProvider, MarketResearchProvider
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.integrations.dataforseo.mock import FixtureMarketResearchProvider
from rank_rent.integrations.domain_availability.mock import MockDomainAvailabilityProvider
from rank_rent.integrations.domain_availability.unknown import UnknownDomainAvailabilityProvider
from rank_rent.runtime import DataMode, validate_runtime_mode
from rank_rent.settings import Settings


def build_market_research_provider(
    settings: Settings,
    mode: DataMode,
) -> MarketResearchProvider:
    validate_runtime_mode(settings, mode)
    if mode == DataMode.fixture:
        return FixtureMarketResearchProvider()
    return DataForSEOLiveProvider(settings=settings)


def build_domain_availability_provider(
    settings: Settings,
    mode: DataMode,
) -> DomainAvailabilityProvider:
    validate_runtime_mode(settings, mode)
    if mode == DataMode.fixture:
        return MockDomainAvailabilityProvider()
    return UnknownDomainAvailabilityProvider()
