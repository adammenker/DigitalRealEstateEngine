from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from rank_rent.db.base import (
    SCHEMA_HEAD_REVISION,
    WebSessionLocal,
    WorkerSessionLocal,
    database_engine_options,
    database_healthcheck,
    database_readiness,
    make_engine,
)
from rank_rent.settings import Settings


def test_production_requires_postgresql() -> None:
    with pytest.raises(ValidationError, match="requires a PostgreSQL"):
        Settings(app_env="production", database_url="sqlite:///production.db")

    settings = Settings(
        app_env="production",
        database_url="postgresql+psycopg://user:password@db/rank_rent",
    )
    assert settings.database_url.startswith("postgresql+")


def test_postgresql_engine_options_are_explicit() -> None:
    settings = Settings(
        database_pool_size=7,
        database_max_overflow=3,
        database_pool_timeout_seconds=12,
        database_pool_recycle_seconds=900,
        database_statement_timeout_ms=4_000,
        database_transaction_timeout_ms=8_000,
    )

    options = database_engine_options(
        "postgresql+psycopg://user:password@db/rank_rent",
        settings,
    )

    assert options["pool_size"] == 7
    assert options["max_overflow"] == 3
    assert options["pool_timeout"] == 12
    assert options["pool_recycle"] == 900
    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {
        "options": "-c statement_timeout=4000 -c idle_in_transaction_session_timeout=8000"
    }


def test_sqlite_health_and_schema_readiness() -> None:
    engine = make_engine("sqlite:///:memory:")
    assert database_healthcheck(engine) is True
    assert database_readiness(engine)["ready"] is False

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": SCHEMA_HEAD_REVISION},
        )

    assert database_readiness(engine) == {
        "ready": True,
        "dialect": "sqlite",
        "schema_revision": SCHEMA_HEAD_REVISION,
    }


def test_web_and_worker_sessions_use_dedicated_factories() -> None:
    assert WebSessionLocal is not WorkerSessionLocal
