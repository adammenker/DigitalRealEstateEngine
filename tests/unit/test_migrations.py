from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError, DBAPIError
from sqlalchemy.orm import Session

from rank_rent.db.base import SCHEMA_HEAD_REVISION, make_engine
from rank_rent.db.orm import (
    FullOpportunityScoreORM,
    JsonArtifactORM,
    MarketORM,
    OpportunityORM,
    ScanRunORM,
    ServiceFamilyORM,
)
from rank_rent.outcomes.orm import PropertyDecisionORM
from rank_rent.settings import get_settings


def test_migration_graph_has_one_linear_head_for_workstreams_c_and_d() -> None:
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
    assert revisions[SCHEMA_HEAD_REVISION] == "8b3e1f4a6c2d"
    assert revisions["8b3e1f4a6c2d"] == "1a7d9c4e6b20"
    assert revisions["1a7d9c4e6b20"] == "a6e2c9f4d7b1"
    assert revisions["a6e2c9f4d7b1"] == "8a7d3f2c1b90"
    assert revisions["8a7d3f2c1b90"] == "6f4c2d8a9b17"
    assert revisions["6f4c2d8a9b17"] == "c9a4e7d2b6f1"
    assert revisions["c9a4e7d2b6f1"] == "f8c1d4e7a2b9"
    assert revisions["f8c1d4e7a2b9"] == "b7d2f4a9c6e1"


def test_alembic_upgrade_head_creates_v1_schema(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "rank_rent.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("script_location", str(Path.cwd() / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "alembic_version" in tables
    assert "raw_api_responses" in tables
    assert "raw_response_cache_entries" in tables
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
        "opportunity_reviews",
        "evidence_overrides",
        "discovery_templates",
        "batch_scan_plans",
        "batch_scan_plan_items",
        "audit_events",
        "worker_heartbeats",
    } <= tables
    opportunity_columns = {
        column["name"] for column in inspector.get_columns("opportunities")
    }
    assert {"owner_user_id", "review_version"} <= opportunity_columns
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO audit_events (
                    event_type, actor_user_id, actor_role, target_type, target_id,
                    request_id, metadata, occurred_at, previous_hash, event_hash
                ) VALUES (
                    'test', 'actor', 'admin', 'scan', '1',
                    'request', '{}', CURRENT_TIMESTAMP, 'GENESIS', 'hash'
                )
                """
            )
        )
    with pytest.raises(DatabaseError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE event_hash = 'hash'")
            )
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
    assert "provider_daily_usage" in tables
    assert "provider_qualifications" in tables
    assert "billing_reconciliations" in tables
    scan_columns = {column["name"] for column in inspector.get_columns("scan_runs")}
    assert {
        "data_mode",
        "scan_profile",
        "planned_cost_usd",
        "progress_stage",
        "worker_id",
        "claimed_at",
        "heartbeat_at",
        "lease_token",
        "lease_expires_at",
        "next_attempt_at",
        "max_attempts",
        "quarantined_at",
        "quarantine_reason",
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
    response_indexes = {
        index["name"]: index for index in inspector.get_indexes("raw_api_responses")
    }
    assert not response_indexes["ix_raw_api_responses_cache_key"]["unique"]
    response_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("raw_api_responses")
    }
    assert ("object_key",) not in response_unique_constraints
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
        "attempt_token",
        "attempt_state",
        "provider_outcome",
        "reservation_state",
        "reservation_usage_date",
        "reservation_usage_class",
        "reservation_estimated_cost_usd",
        "network_started_at",
        "reconciled_at",
        "execution_worker_id",
        "execution_lease_token",
    } <= api_call_columns
    usage_columns = {
        column["name"] for column in inspector.get_columns("provider_daily_usage")
    }
    assert "unreconciled_spend_usd" in usage_columns
    qualification_columns = {
        column["name"] for column in inspector.get_columns("provider_qualifications")
    }
    assert {
        "execution_method",
        "gate_eligible",
        "evidence_sha256",
        "executed_by",
        "override_reason",
    } <= qualification_columns
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
    delivery_columns = {
        column["name"] for column in inspector.get_columns("provider_deliveries")
    }
    assert {
        "max_attempts",
        "next_attempt_at",
        "worker_id",
        "lease_token",
        "claimed_at",
        "heartbeat_at",
        "lease_expires_at",
        "last_error_code",
        "last_error_summary",
        "completed_at",
    } <= delivery_columns
    artifact_columns = {column["name"] for column in inspector.get_columns("json_artifacts")}
    assert "scan_run_id" in artifact_columns
    decision_columns = {
        column["name"] for column in inspector.get_columns("property_decisions")
    }
    assert "scan_run_id" in decision_columns
    with create_engine(f"sqlite:///{db_path}").connect() as connection:
        trigger_names = set(
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'property_decisions'"
            ).scalars()
        )
    assert trigger_names == {
        "property_decisions_immutable_update",
        "property_decisions_immutable_delete",
    }


def test_migrated_database_rejects_direct_property_decision_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "immutable-decisions.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("script_location", str(Path.cwd() / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = make_engine(database_url)
    with Session(engine, expire_on_commit=False) as session:
        service = ServiceFamilyORM(slug="immutable-service", display_name="Immutable")
        market = MarketORM(slug="immutable-market", display_name="Immutable Market")
        session.add_all([service, market])
        session.flush()
        opportunity = OpportunityORM(
            service_family_id=service.id,
            market_id=market.id,
            status="approved",
        )
        scan = ScanRunORM(source="fixture", status="completed")
        session.add_all([opportunity, scan])
        session.flush()
        score = FullOpportunityScoreORM(
            scan_run_id=scan.id,
            opportunity_id=opportunity.id,
            scoring_version="immutable-v1",
            total_score=70,
            confidence="medium",
            explanation="Fixture.",
            payload={},
        )
        artifact = JsonArtifactORM(
            opportunity_id=opportunity.id,
            scan_run_id=scan.id,
            kind="scan_result",
            payload={"assessment_type": "full"},
        )
        session.add_all([score, artifact])
        session.flush()
        decision = PropertyDecisionORM(
            property_id="immutable-property",
            opportunity_id=opportunity.id,
            scan_run_id=scan.id,
            full_score_id=score.id,
            evidence_snapshot_id=artifact.id,
            score_version_at_selection=score.scoring_version,
            selected_at=datetime.now(UTC),
            service_family_slug=service.slug,
            market_size_band="medium",
            evidence_quality="pass",
            validated_opportunity_cost_usd=0,
            selection_context={},
        )
        session.add(decision)
        session.commit()
        decision_id = decision.id

    with pytest.raises(DBAPIError, match="property_decision_is_immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE property_decisions "
                    "SET evidence_quality = 'warn' WHERE id = :decision_id"
                ),
                {"decision_id": decision_id},
            )
    with pytest.raises(DBAPIError, match="property_decision_is_immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM property_decisions WHERE id = :decision_id"),
                {"decision_id": decision_id},
            )


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
        pointer = connection.execute(
            text(
                """
                SELECT raw_api_response_id
                FROM raw_response_cache_entries
                WHERE cache_key = :cache_key
                """
            ),
            {"cache_key": "legacy-cache-key"},
        ).scalar_one()
    assert row.response_json == '{"tasks": []}'
    assert row.object_key is None
    assert row.retention_classification == "raw_provider_response"
    assert row.encryption_status == "not_encrypted"
    assert pointer > 0


def test_worker_recovery_upgrade_conservatively_backfills_legacy_calls(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "worker-upgrade.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("script_location", str(Path.cwd() / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "6f4c2d8a9b17")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        for cache_key, status in (
            ("legacy-completed", "completed"),
            ("legacy-running", "running"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO api_calls (
                        provider, endpoint, stage, cache_key, cache_hit, force_refresh,
                        estimated_cost_usd, actual_cost_usd, status
                    ) VALUES (
                        'dataforseo-live', '/legacy', 'legacy', :cache_key, false, false,
                        0.25, 0, :status
                    )
                    """
                ),
                {"cache_key": cache_key, "status": status},
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT cache_key, status, attempt_state, provider_outcome
                FROM api_calls ORDER BY cache_key
                """
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("legacy-completed", "completed", "completed", "completed"),
        (
            "legacy-running",
            "provider_outcome_unknown",
            "provider_outcome_unknown",
            "unknown",
        ),
    ]
