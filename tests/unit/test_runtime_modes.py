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
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
    )
    provider = build_market_research_provider(settings, DataMode.live)
    domain_provider = build_domain_availability_provider(settings, DataMode.live)
    assert isinstance(provider, DataForSEOLiveProvider)
    assert not isinstance(provider, FixtureMarketResearchProvider)
    assert isinstance(domain_provider, DNSDomainAvailabilityProvider)
    assert not isinstance(domain_provider, MockDomainAvailabilityProvider)


def test_live_mode_uses_sandbox_environment_by_default() -> None:
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
    )
    provider = build_market_research_provider(settings, DataMode.live)

    assert isinstance(provider, DataForSEOLiveProvider)
    assert provider.provider_name == "dataforseo-sandbox"
    assert provider.base_url == "https://sandbox.dataforseo.com"


def test_live_mode_can_explicitly_use_production_environment() -> None:
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="production",
    )
    provider = build_market_research_provider(settings, DataMode.live)

    assert isinstance(provider, DataForSEOLiveProvider)
    assert provider.provider_name == "dataforseo-live"
    assert provider.base_url == "https://api.dataforseo.com"


def test_testing_scan_can_initialize_when_global_default_is_full_but_full_is_disabled() -> None:
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_full_scans=False,
        live_scan_depth="full",
        dataforseo_login="user",
        dataforseo_password="password",
    )

    provider = build_market_research_provider(settings, DataMode.live)

    assert isinstance(provider, DataForSEOLiveProvider)


def test_replay_mode_does_not_require_live_credentials() -> None:
    settings = Settings(data_mode="replay", allow_live_api_calls=False)
    validate_runtime_mode(settings, DataMode.replay)
    provider = build_market_research_provider(
        settings,
        DataMode.replay,
        replay_transport=BundleReplayTransport([]),
    )
    assert isinstance(provider, DataForSEOReplayProvider)
