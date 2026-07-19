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
    assert {
        "data_mode",
        "scan_profile",
        "planned_cost_usd",
        "progress_stage",
        "worker_id",
        "claimed_at",
        "heartbeat_at",
    } <= scan_columns
    response_columns = {column["name"] for column in inspector.get_columns("raw_api_responses")}
    assert {"response_shape_version", "sanitized", "checksum", "expires_at"} <= response_columns
    service_columns = {column["name"] for column in inspector.get_columns("service_families")}
    assert {"intent_modifiers", "negative_product_terms"} <= service_columns
    api_call_columns = {column["name"] for column in inspector.get_columns("api_calls")}
    assert {
        "planned_request_id",
        "started_at",
        "completed_at",
        "provider_task_id",
        "provider_request_id",
        "error_type",
    } <= api_call_columns
    api_call_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("api_calls")
    }
    plan_call_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("scan_plan_calls")
    }
    assert ("scan_run_id", "planned_request_id") in api_call_unique_constraints
    assert ("scan_run_id", "planned_request_id") in plan_call_unique_constraints
    market_columns = {column["name"] for column in inspector.get_columns("markets")}
    assert {
        "county",
        "county_fips",
        "metro",
        "metro_code",
        "population",
        "reference_population",
        "aliases",
        "boundary_radius_km",
        "geography_id",
        "geography_dataset_version",
    } <= market_columns
    serp_columns = {column["name"] for column in inspector.get_columns("serp_results")}
    assert {
        "classification_confidence",
        "classifier_version",
        "matched_rules",
        "classification_evidence",
    } <= serp_columns
    provider_columns = {
        column["name"] for column in inspector.get_columns("provider_candidates")
    }
    assert {
        "categories",
        "latitude",
        "longitude",
        "source_timestamp",
        "suitability_signals",
    } <= provider_columns
