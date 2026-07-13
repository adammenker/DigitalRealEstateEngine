import pytest

from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.integrations.dataforseo.mock import FixtureMarketResearchProvider
from rank_rent.integrations.dataforseo.replay import DataForSEOReplayProvider
from rank_rent.integrations.domain_availability.dns import DNSDomainAvailabilityProvider
from rank_rent.integrations.domain_availability.mock import MockDomainAvailabilityProvider
from rank_rent.integrations.factory import (
    build_domain_availability_provider,
    build_market_research_provider,
)
from rank_rent.replay import BundleReplayTransport
from rank_rent.runtime import ConfigurationError, DataMode, validate_runtime_mode
from rank_rent.settings import Settings


def test_fixture_mode_uses_fixture_adapter() -> None:
    settings = Settings(data_mode="fixture")
    provider = build_market_research_provider(settings, DataMode.fixture)
    domain_provider = build_domain_availability_provider(settings, DataMode.fixture)
    assert isinstance(provider, FixtureMarketResearchProvider)
    assert isinstance(domain_provider, MockDomainAvailabilityProvider)


def test_live_mode_missing_credentials_fails_fast() -> None:
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="",
        dataforseo_password="",
    )
    with pytest.raises(ConfigurationError, match="DATAFORSEO_LOGIN"):
        validate_runtime_mode(settings, DataMode.live)


def test_live_mode_never_instantiates_mock_adapter() -> None:
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
    )
    provider = build_market_research_provider(settings, DataMode.live)
    domain_provider = build_domain_availability_provider(settings, DataMode.live)
    assert isinstance(provider, DataForSEOLiveProvider)
    assert not isinstance(provider, FixtureMarketResearchProvider)
    assert isinstance(domain_provider, DNSDomainAvailabilityProvider)
    assert not isinstance(domain_provider, MockDomainAvailabilityProvider)


def test_replay_mode_does_not_require_live_credentials() -> None:
    settings = Settings(data_mode="replay", allow_live_api_calls=False)
    validate_runtime_mode(settings, DataMode.replay)
    provider = build_market_research_provider(
        settings,
        DataMode.replay,
        replay_transport=BundleReplayTransport([]),
    )
    assert isinstance(provider, DataForSEOReplayProvider)
