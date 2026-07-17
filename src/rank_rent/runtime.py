from __future__ import annotations

from enum import StrEnum

from rank_rent.settings import Settings


class DataMode(StrEnum):
    fixture = "fixture"
    live = "live"
    replay = "replay"


class ConfigurationError(RuntimeError):
    pass


def resolve_data_mode(value: str | DataMode | None) -> DataMode:
    if isinstance(value, DataMode):
        return value
    normalized = (value or DataMode.fixture.value).strip().lower()
    try:
        return DataMode(normalized)
    except ValueError as exc:
        valid = ", ".join(mode.value for mode in DataMode)
        raise ConfigurationError(f"Invalid DATA_MODE '{value}'. Expected one of: {valid}.") from exc


def validate_runtime_mode(settings: Settings, mode: DataMode | None = None) -> DataMode:
    data_mode = mode or resolve_data_mode(settings.data_mode)
    if data_mode == DataMode.live:
        missing: list[str] = []
        environment = settings.dataforseo_environment.strip().lower()
        if environment not in {"sandbox", "production"}:
            missing.append("DATAFORSEO_ENVIRONMENT=sandbox|production")
        if not settings.allow_live_api_calls:
            missing.append("ALLOW_LIVE_API_CALLS=true")
        if not settings.dataforseo_login:
            missing.append("DATAFORSEO_LOGIN")
        if not settings.dataforseo_password:
            missing.append("DATAFORSEO_PASSWORD")
        if missing:
            raise ConfigurationError(
                "Live mode requires explicit DataForSEO configuration. Missing: "
                + ", ".join(missing)
            )
    return data_mode
