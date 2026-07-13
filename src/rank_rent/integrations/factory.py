from __future__ import annotations

from sqlalchemy.orm import Session

from rank_rent.domain.interfaces import DomainAvailabilityProvider, MarketResearchProvider
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.integrations.dataforseo.mock import FixtureMarketResearchProvider
from rank_rent.integrations.dataforseo.replay import DataForSEOReplayProvider
from rank_rent.integrations.domain_availability.dns import DNSDomainAvailabilityProvider
from rank_rent.integrations.domain_availability.mock import MockDomainAvailabilityProvider
from rank_rent.replay import ReplayTransport
from rank_rent.runtime import DataMode, validate_runtime_mode
from rank_rent.settings import Settings


def build_market_research_provider(
    settings: Settings,
    mode: DataMode,
    *,
    replay_transport: ReplayTransport | None = None,
    session: Session | None = None,
) -> MarketResearchProvider:
    validate_runtime_mode(settings, mode)
    if mode == DataMode.fixture:
        return FixtureMarketResearchProvider()
    if mode == DataMode.replay:
        if replay_transport is None:
            raise ValueError("Replay mode requires a replay transport.")
        return DataForSEOReplayProvider(replay_transport, settings=settings)
    return DataForSEOLiveProvider(settings=settings, session=session)


def build_domain_availability_provider(
    settings: Settings,
    mode: DataMode,
) -> DomainAvailabilityProvider:
    validate_runtime_mode(settings, mode)
    if mode == DataMode.fixture:
        return MockDomainAvailabilityProvider()
    return DNSDomainAvailabilityProvider()
