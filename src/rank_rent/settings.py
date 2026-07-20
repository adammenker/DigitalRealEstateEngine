from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    service_name: str = "rank-rent-api"
    log_level: str = "INFO"
    data_mode: str = "fixture"
    database_url: str = "sqlite:///./rank_rent.db"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, ge=0)
    database_statement_timeout_ms: int = Field(default=30_000, ge=1)
    database_transaction_timeout_ms: int = Field(default=60_000, ge=1)
    database_sqlite_busy_timeout_ms: int = Field(default=5_000, ge=0)
    blob_store_backend: Literal["filesystem", "s3"] = "filesystem"
    blob_store_path: Path = Path(".cache/raw-responses")
    blob_store_s3_bucket: str = ""
    blob_store_s3_prefix: str = "rank-rent"
    blob_store_s3_endpoint_url: str = ""
    blob_store_s3_region: str = ""
    blob_store_s3_server_side_encryption: str = "AES256"
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_environment: str = "sandbox"
    whoisxml_api_key: str = ""
    pexels_api_key: str = ""
    hunter_api_key: str = ""
    openai_api_key: str = ""
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""
    cloudflare_pages_project: str = ""
    max_scan_cost_usd: float = Field(default=10.0, ge=0)
    max_scan_requests: int = Field(default=15, ge=0)
    allow_live_api_calls: bool = False
    allow_production_dataforseo: bool = False
    paid_call_kill_switch: bool = False
    allow_full_scans: bool = False
    live_scan_depth: str = "testing"
    us_geography_database_path: Path = Path("data/us_geography.sqlite3")
    scan_worker_concurrency: int = Field(default=1, ge=1, le=32)
    scan_worker_poll_seconds: float = Field(default=1.0, ge=0.1)
    scan_worker_heartbeat_seconds: float = Field(default=5.0, ge=0.5)
    scan_worker_stale_after_seconds: float = Field(default=30.0, ge=1.0)
    scan_worker_long_running_seconds: float = Field(default=3600.0, ge=1.0)
    scan_worker_max_attempts: int = Field(default=4, ge=1, le=20)
    scan_worker_retry_base_seconds: float = Field(default=2.0, ge=0.1)
    scan_worker_retry_max_seconds: float = Field(default=300.0, ge=1.0)
    production_daily_request_limit: int = Field(default=100, ge=0)
    production_daily_spend_usd: float = Field(default=25.0, ge=0)
    testing_daily_spend_usd: float = Field(default=2.0, ge=0)
    single_call_abnormal_cost_usd: float = Field(default=1.0, ge=0)
    unexpected_call_breaker_threshold: int = Field(default=3, ge=1)
    provider_failure_rate_threshold: float = Field(default=0.5, ge=0, le=1)
    schema_drift_rate_threshold: float = Field(default=0.1, ge=0, le=1)
    circuit_breaker_minimum_requests: int = Field(default=5, ge=1)
    qualification_ttl_hours: int = Field(default=168, ge=1)
    billing_reconciliation_max_age_hours: int = Field(default=48, ge=1)
    billing_reconciliation_tolerance_usd: float = Field(default=0.01, ge=0)
    worker_required: bool = False
    auth_mode: str = "local"
    local_auth_enabled: bool = True
    secrets_injected_by_platform: bool = False
    local_auth_default_user: str = "local-admin"
    local_auth_default_role: str = "admin"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_roles_claim: str = "roles"
    oidc_allowed_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    oidc_allowed_jwks_hosts: list[str] = Field(default_factory=list)
    oidc_jwks_cache_seconds: int = Field(default=300, ge=30, le=86400)
    outbound_http_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:8010", "http://localhost:8010"]
    )
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    rate_limit_requests: int = Field(default=120, ge=1, le=100_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_backend: str = "memory"
    redis_url: str = ""
    content_security_policy: str = (
        "default-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    release_git_sha: str = "development"
    release_image_digest: str = "unavailable"
    release_frontend_image_digest: str = "unavailable"
    release_notes: str = ""
    geography_dataset_version: str = "bundled"
    project_root: Path = Path.cwd()

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"development", "local", "test", "staging", "production"}:
            raise ValueError("APP_ENV must be local, test, staging, or production.")
        return normalized

    @field_validator("auth_mode")
    @classmethod
    def validate_auth_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"local", "oidc"}:
            raise ValueError("AUTH_MODE must be local or oidc.")
        return normalized

    @field_validator("rate_limit_backend")
    @classmethod
    def validate_rate_limit_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"memory", "redis"}:
            raise ValueError("RATE_LIMIT_BACKEND must be memory or redis.")
        return normalized

    @property
    def worker_stale_after(self) -> timedelta:
        return timedelta(seconds=self.scan_worker_stale_after_seconds)

    @model_validator(mode="after")
    def validate_production_storage(self) -> Settings:
        if self.app_env == "production" and not self.database_url.startswith(
            ("postgresql://", "postgresql+")
        ):
            raise ValueError("APP_ENV=production requires a PostgreSQL DATABASE_URL.")
        if self.blob_store_backend == "s3" and not self.blob_store_s3_bucket.strip():
            raise ValueError("BLOB_STORE_S3_BUCKET is required when BLOB_STORE_BACKEND=s3.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
