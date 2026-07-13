import pytest

from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.integrations.dataforseo.mock import FixtureMarketResearchProvider
from rank_rent.integrations.factory import build_market_research_provider
from rank_rent.runtime import ConfigurationError, DataMode, validate_runtime_mode
from rank_rent.settings import Settings


def test_fixture_mode_uses_fixture_adapter() -> None:
    settings = Settings(data_mode="fixture")
    provider = build_market_research_provider(settings, DataMode.fixture)
    assert isinstance(provider, FixtureMarketResearchProvider)


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
    assert isinstance(provider, DataForSEOLiveProvider)
    assert not isinstance(provider, FixtureMarketResearchProvider)

