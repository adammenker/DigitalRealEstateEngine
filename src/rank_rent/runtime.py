from __future__ import annotations

from enum import StrEnum

from rank_rent.security.secrets import is_secret_reference
from rank_rent.settings import Settings


class DataMode(StrEnum):
    fixture = "fixture"
    live = "live"
    replay = "replay"


class ConfigurationError(RuntimeError):
    pass


def validate_environment(settings: Settings) -> None:
    """Fail closed when staging/production isolation or auth is incomplete."""
    if settings.app_env not in {"staging", "production"}:
        return
    failures: list[str] = []
    if settings.auth_mode != "oidc":
        failures.append("AUTH_MODE=oidc")
    if settings.local_auth_enabled:
        failures.append("LOCAL_AUTH_ENABLED=false")
    if not settings.oidc_issuer.startswith("https://"):
        failures.append("OIDC_ISSUER=https://...")
    if not settings.oidc_audience:
        failures.append("OIDC_AUDIENCE")
    if not settings.oidc_jwks_url.startswith("https://"):
        failures.append("OIDC_JWKS_URL=https://...")
    if not settings.oidc_allowed_jwks_hosts:
        failures.append("OIDC_ALLOWED_JWKS_HOSTS")
    if not settings.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        failures.append("a dedicated PostgreSQL DATABASE_URL")
    if settings.blob_store_backend != "s3" or not settings.blob_store_s3_bucket:
        failures.append("dedicated S3 blob storage")
    if not settings.cors_allowed_origins or any(
        not origin.startswith("https://") for origin in settings.cors_allowed_origins
    ):
        failures.append("HTTPS-only CORS_ALLOWED_ORIGINS")
    if settings.rate_limit_backend != "redis":
        failures.append("RATE_LIMIT_BACKEND=redis")
    if not settings.redis_url.startswith("rediss://"):
        failures.append("a TLS REDIS_URL")
    if (
        settings.app_env == "production"
        and resolve_data_mode(settings.data_mode) == DataMode.live
        and settings.dataforseo_environment != "production"
    ):
        failures.append("DATAFORSEO_ENVIRONMENT=production or DATA_MODE=fixture/replay")
    secret_values = {
        "DATAFORSEO_PASSWORD": settings.dataforseo_password,
        "WHOISXML_API_KEY": settings.whoisxml_api_key,
        "PEXELS_API_KEY": settings.pexels_api_key,
        "HUNTER_API_KEY": settings.hunter_api_key,
        "OPENAI_API_KEY": settings.openai_api_key,
        "CLOUDFLARE_API_TOKEN": settings.cloudflare_api_token,
        "REDIS_URL": settings.redis_url,
    }
    plaintext = [
        name
        for name, value in secret_values.items()
        if value and not is_secret_reference(value)
    ]
    if plaintext and not settings.secrets_injected_by_platform:
        failures.append(
            "secret references for configured credentials: " + ", ".join(sorted(plaintext))
        )
    if failures:
        raise ConfigurationError(
            f"{settings.app_env.title()} environment validation failed: "
            + "; ".join(failures)
        )


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
        if settings.paid_call_kill_switch:
            missing.append("PAID_CALL_KILL_SWITCH=false")
        if environment == "production" and not settings.allow_production_dataforseo:
            missing.append("ALLOW_PRODUCTION_DATAFORSEO=true")
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
