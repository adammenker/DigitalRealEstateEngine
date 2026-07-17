from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from rank_rent.settings import get_settings


def test_alembic_upgrade_head_creates_v1_schema(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "rank_rent.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("script_location", str(Path.cwd() / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{db_path}"))
    tables = set(inspector.get_table_names())
    assert "alembic_version" in tables
    assert "raw_api_responses" in tables
    assert "preliminary_assessments" in tables
    assert "full_opportunity_scores" in tables
    assert "api_calls" in tables
    scan_columns = {column["name"] for column in inspector.get_columns("scan_runs")}
    assert {"data_mode", "scan_profile", "planned_cost_usd", "progress_stage"} <= scan_columns
    response_columns = {column["name"] for column in inspector.get_columns("raw_api_responses")}
    assert {"response_shape_version", "sanitized", "checksum", "expires_at"} <= response_columns
