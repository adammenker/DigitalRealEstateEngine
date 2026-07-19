from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    data_mode: str = "fixture"
    database_url: str = "sqlite:///./rank_rent.db"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
