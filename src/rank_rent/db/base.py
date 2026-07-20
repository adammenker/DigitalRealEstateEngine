from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy import inspect as inspect_database
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from rank_rent.settings import Settings, get_settings

BASELINE_REVISION = "e6f6b8c2a915"
SCHEMA_HEAD_REVISION = "6a1c9e4b7d20"


class Base(DeclarativeBase):
    pass


def database_engine_options(
    database_url: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    configured = settings or get_settings()
    backend = make_url(database_url).get_backend_name()
    if backend == "sqlite":
        return {
            "connect_args": {
                "check_same_thread": False,
                "timeout": configured.database_sqlite_busy_timeout_ms / 1000,
            },
            "pool_pre_ping": True,
        }
    if backend == "postgresql":
        return {
            "connect_args": {
                "options": (
                    f"-c statement_timeout={configured.database_statement_timeout_ms} "
                    "-c idle_in_transaction_session_timeout="
                    f"{configured.database_transaction_timeout_ms}"
                )
            },
            "pool_size": configured.database_pool_size,
            "max_overflow": configured.database_max_overflow,
            "pool_timeout": configured.database_pool_timeout_seconds,
            "pool_recycle": configured.database_pool_recycle_seconds,
            "pool_pre_ping": True,
        }
    raise ValueError("Only SQLite and PostgreSQL database URLs are supported.")


def make_engine(database_url: str | None = None, settings: Settings | None = None) -> Engine:
    configured = settings or get_settings()
    url = database_url or configured.database_url
    created_engine = create_engine(url, **database_engine_options(url, configured))
    if make_url(url).get_backend_name() == "sqlite":
        event.listen(created_engine, "connect", _enable_sqlite_foreign_keys)
    return created_engine


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


engine = make_engine()
WebSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
WorkerSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
# Backward-compatible alias for CLI and existing integrations. Web requests and workers use
# distinct factories so a Session is never shared between execution contexts.
SessionLocal = WebSessionLocal


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _alembic_config(database_url: str) -> Config:
    root = _repo_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _is_ephemeral_sqlite(database_url: str) -> bool:
    return database_url in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in database_url


def init_db() -> None:
    from rank_rent.db import orm  # noqa: F401
    from rank_rent.lead_routing import orm as lead_routing_orm  # noqa: F401
    from rank_rent.opportunity_review import orm as opportunity_review_orm  # noqa: F401
    from rank_rent.outcomes import orm as outcomes_orm  # noqa: F401

    settings = get_settings()
    database_url = settings.database_url
    if _is_ephemeral_sqlite(database_url) or not (_repo_root() / "alembic.ini").exists():
        Base.metadata.create_all(bind=engine)
        return

    config = _alembic_config(database_url)
    tables = set(inspect_database(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        if settings.app_env.strip().lower() == "production":
            raise RuntimeError(
                "Refusing to stamp an unversioned production database. "
                "Restore a versioned database or perform an explicit cutover."
            )
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


def reset_db() -> None:
    from rank_rent.db import orm  # noqa: F401
    from rank_rent.lead_routing import orm as lead_routing_orm  # noqa: F401
    from rank_rent.opportunity_review import orm as opportunity_review_orm  # noqa: F401
    from rank_rent.outcomes import orm as outcomes_orm  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db()


def database_healthcheck(bind: Engine = engine) -> bool:
    try:
        with bind.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


def database_readiness(bind: Engine = engine) -> dict[str, str | bool | None]:
    status: dict[str, str | bool | None] = {
        "ready": False,
        "dialect": bind.dialect.name,
        "schema_revision": None,
    }
    try:
        with bind.connect() as connection:
            connection.execute(text("SELECT 1"))
            if "alembic_version" not in inspect_database(connection).get_table_names():
                return status
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        return status
    status["schema_revision"] = str(revision) if revision is not None else None
    status["ready"] = revision == SCHEMA_HEAD_REVISION
    return status


def get_session() -> Generator[Session]:
    session = WebSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def worker_session() -> Generator[Session]:
    session = WorkerSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
