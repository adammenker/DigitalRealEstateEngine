from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
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
    live_scan_depth: str = "testing"
    us_geography_database_path: Path = Path("data/us_geography.sqlite3")
    scan_worker_enabled: bool = True
    scan_worker_poll_seconds: float = Field(default=1.0, ge=0.1)
    scan_worker_heartbeat_seconds: float = Field(default=5.0, ge=0.5)
    scan_worker_stale_after_seconds: float = Field(default=30.0, ge=1.0)
    project_root: Path = Path.cwd()

    @model_validator(mode="after")
    def validate_production_storage(self) -> Settings:
        if self.app_env.strip().lower() == "production" and not self.database_url.startswith(
            ("postgresql://", "postgresql+")
        ):
            raise ValueError("APP_ENV=production requires a PostgreSQL DATABASE_URL.")
        if self.blob_store_backend == "s3" and not self.blob_store_s3_bucket.strip():
            raise ValueError("BLOB_STORE_S3_BUCKET is required when BLOB_STORE_BACKEND=s3.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
