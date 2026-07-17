from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy import inspect as inspect_database
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from rank_rent.settings import get_settings

BASELINE_REVISION = "e6f6b8c2a915"


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


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

    database_url = get_settings().database_url
    if _is_ephemeral_sqlite(database_url) or not (_repo_root() / "alembic.ini").exists():
        Base.metadata.create_all(bind=engine)
        return

    config = _alembic_config(database_url)
    tables = set(inspect_database(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


def reset_db() -> None:
    from rank_rent.db import orm  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db()


def get_session() -> Generator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
