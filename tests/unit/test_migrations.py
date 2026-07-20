from __future__ import annotations

import ast
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from rank_rent.db.base import SCHEMA_HEAD_REVISION
from rank_rent.settings import get_settings


def test_migration_graph_has_one_head_and_workstream_c_follows_prior_head() -> None:
    revisions: dict[str, str | None] = {}
    for path in Path("migrations/versions").glob("*.py"):
        values: dict[str, str | None] = {}
        for node in ast.parse(path.read_text()).body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"revision", "down_revision"}
            ):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        revisions[str(values["revision"])] = values.get("down_revision")

    referenced = {revision for revision in revisions.values() if revision is not None}
    assert set(revisions) - referenced == {SCHEMA_HEAD_REVISION}
    assert revisions[SCHEMA_HEAD_REVISION] == "b7d2f4a9c6e1"


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
    assert "market_prefilter_runs" in tables
    assert "market_prefilter_assessments" in tables
    assert "api_calls" in tables
    assert {
        "property_routing_profiles",
        "provider_assignments",
        "leads",
        "lead_events",
        "consent_records",
        "spam_assessments",
        "routing_attempts",
        "provider_deliveries",
        "lead_outcomes",
        "analytics_events",
        "property_decisions",
        "property_outcomes",
        "calibration_reports",
        "scoring_change_reviews",
    } <= tables
    routing_profile_columns = {
        column["name"] for column in inspector.get_columns("property_routing_profiles")
    }
    assert {
        "public_tracking_number",
        "call_adapter_name",
        "call_provider_route_id",
        "routing_health_status",
        "routing_health_checked_at",
    } <= routing_profile_columns
    provider_assignment_indexes = {
        index["name"] for index in inspector.get_indexes("provider_assignments")
    }
    assert "uq_provider_assignments_active_property" in provider_assignment_indexes
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
    assert {
        "response_shape_version",
        "sanitized",
        "checksum",
        "expires_at",
        "object_key",
        "storage_backend",
        "content_type",
        "size_bytes",
        "retention_classification",
        "encryption_status",
        "blob_created_at",
    } <= response_columns
    with create_engine(f"sqlite:///{db_path}").connect() as connection:
        revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    assert revision == SCHEMA_HEAD_REVISION
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
    provider_columns = {column["name"] for column in inspector.get_columns("provider_candidates")}
    assert {
        "categories",
        "latitude",
        "longitude",
        "source_timestamp",
        "suitability_signals",
    } <= provider_columns
    competitor_columns = {column["name"] for column in inspector.get_columns("competitor_metrics")}
    assert {
        "representative_query",
        "serp_position",
        "serp_observations",
    } <= competitor_columns


def test_workstream_c_upgrade_preserves_populated_legacy_raw_response(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "upgrade.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("script_location", str(Path.cwd() / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "b7d2f4a9c6e1")

    engine = create_engine(database_url)
    now = "2026-07-19 12:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO raw_api_responses (
                    cache_key, provider, endpoint, parameters, api_version,
                    response_json, status_code, request_time, response_time,
                    cost_usd, created_at, updated_at, response_shape_version,
                    sanitized, checksum
                ) VALUES (
                    :cache_key, :provider, :endpoint, :parameters, :api_version,
                    :response_json, :status_code, :request_time, :response_time,
                    :cost_usd, :created_at, :updated_at, :response_shape_version,
                    :sanitized, :checksum
                )
                """
            ),
            {
                "cache_key": "legacy-cache-key",
                "provider": "dataforseo-fixture",
                "endpoint": "/legacy",
                "parameters": "{}",
                "api_version": "v3",
                "response_json": '{"tasks": []}',
                "status_code": 200,
                "request_time": now,
                "response_time": now,
                "cost_usd": 0,
                "created_at": now,
                "updated_at": now,
                "response_shape_version": "v1",
                "sanitized": True,
                "checksum": "",
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT response_json, object_key, retention_classification, encryption_status
                FROM raw_api_responses WHERE cache_key = :cache_key
                """
            ),
            {"cache_key": "legacy-cache-key"},
        ).one()
    assert row.response_json == '{"tasks": []}'
    assert row.object_key is None
    assert row.retention_classification == "raw_provider_response"
    assert row.encryption_status == "not_encrypted"
