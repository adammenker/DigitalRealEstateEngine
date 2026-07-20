from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from scripts.check_licenses import _is_denied, denied_licenses
from scripts.release_manifest import build_manifest, verify_manifest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import AuditEventORM
from rank_rent.observability.incidents import SYNTHETIC_INCIDENTS
from rank_rent.observability.logging import JSONFormatter, redact
from rank_rent.runtime import ConfigurationError, validate_environment
from rank_rent.security.audit import ImmutableAuditRecordError, append_audit_event
from rank_rent.security.auth import (
    OIDCVerifier,
    Permission,
    Principal,
    Role,
    require_permission,
)
from rank_rent.security.middleware import (
    RedisFixedWindowRateLimiter,
    SecurityObservabilityMiddleware,
)
from rank_rent.security.secrets import SecretResolutionError, resolve_secret_reference
from rank_rent.security.ssrf import UnsafeURLError, validate_outbound_url
from rank_rent.settings import Settings


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityObservabilityMiddleware, settings=settings)

    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/private")
    def private() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/scans")
    def scan(request: Request) -> dict[str, str]:
        require_permission(request, Permission.run_testing_scan)
        return {"status": "queued"}

    @app.post("/api/opportunities/{opportunity_id}/review/transition")
    def review_transition(opportunity_id: int) -> dict[str, int]:
        return {"opportunity_id": opportunity_id}

    @app.put("/api/discovery-templates/{template_id}")
    def update_template(template_id: int) -> dict[str, int]:
        return {"template_id": template_id}

    return app


def _production_settings() -> Settings:
    return Settings(
        app_env="production",
        auth_mode="oidc",
        local_auth_enabled=False,
        secrets_injected_by_platform=True,
        oidc_issuer="https://identity.example.com",
        oidc_audience="rank-rent",
        oidc_jwks_url="https://identity.example.com/.well-known/jwks.json",
        oidc_allowed_jwks_hosts=["identity.example.com"],
        database_url="postgresql+psycopg://runtime@db.example.com/rank_rent",
        blob_store_backend="s3",
        blob_store_s3_bucket="rank-rent-production",
        cors_allowed_origins=["https://console.example.com"],
        dataforseo_environment="production",
    )


def test_production_routes_fail_closed_but_health_is_public() -> None:
    with TestClient(_app(_production_settings())) as client:
        assert client.get("/live").status_code == 200
        response = client.get("/private")
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication is required."
        assert "request_id" in response.json()


def test_valid_oidc_principal_gets_security_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OIDCVerifier,
        "verify",
        lambda _self, _token: Principal("operator-1", Role.operator),
    )
    with TestClient(_app(_production_settings())) as client:
        response = client.get("/private", headers={"Authorization": "Bearer test"})
    assert response.status_code == 200
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["traceparent"].startswith("00-")


def test_read_only_user_cannot_trigger_scan() -> None:
    settings = Settings(app_env="test", rate_limit_requests=10)
    with TestClient(_app(settings)) as client:
        response = client.post(
            "/api/scans",
            headers={"X-Local-Role": "read_only"},
            json={},
        )
    assert response.status_code == 403


def test_review_and_configuration_mutations_enforce_distinct_roles() -> None:
    settings = Settings(app_env="test", rate_limit_requests=10)
    with TestClient(_app(settings)) as client:
        operator_review = client.post(
            "/api/opportunities/7/review/transition",
            headers={"X-Local-Role": "operator"},
        )
        reviewer_review = client.post(
            "/api/opportunities/7/review/transition",
            headers={"X-Local-Role": "reviewer"},
        )
        operator_config = client.put(
            "/api/discovery-templates/4",
            headers={"X-Local-Role": "operator"},
        )
        admin_config = client.put(
            "/api/discovery-templates/4",
            headers={"X-Local-Role": "admin"},
        )
    assert operator_review.status_code == 403
    assert reviewer_review.status_code == 200
    assert operator_config.status_code == 403
    assert admin_config.status_code == 200


def test_rate_and_request_size_limits() -> None:
    settings = Settings(
        app_env="test",
        rate_limit_requests=1,
        rate_limit_window_seconds=60,
        max_request_body_bytes=1024,
    )
    with TestClient(_app(settings)) as client:
        assert client.get("/private").status_code == 200
        assert client.get("/private").status_code == 429
    size_settings = settings.model_copy(update={"rate_limit_requests": 10})
    with TestClient(_app(size_settings)) as client:
        response = client.post(
            "/api/scans",
            content=b"x" * 1025,
            headers={"content-type": "application/octet-stream"},
        )
    assert response.status_code == 413


def test_append_only_audit_chain_records_actor_and_target() -> None:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    actor = Principal("admin-1", Role.admin)
    with Session() as session:
        first = append_audit_event(
            session,
            event_type="scan.create",
            actor=actor,
            target_type="scan_run",
            target_id="12",
            request_id="request-1",
        )
        session.commit()
        second = append_audit_event(
            session,
            event_type="scan.cost_confirmed",
            actor=actor,
            target_type="scan_run",
            target_id="12",
            request_id="request-1",
        )
        session.commit()
        assert second.previous_hash == first.event_hash
        assert second.actor_user_id == "admin-1"
        assert second.target_id == "12"
        second.event_type = "tampered"
        with pytest.raises(ImmutableAuditRecordError):
            session.flush()


def test_environment_isolation_rejects_local_auth_and_plain_secrets() -> None:
    settings = _production_settings().model_copy(
        update={
            "local_auth_enabled": True,
            "dataforseo_password": "plain-secret",
            "secrets_injected_by_platform": False,
        }
    )
    with pytest.raises(ConfigurationError, match="LOCAL_AUTH_ENABLED=false"):
        validate_environment(settings)


def test_complete_production_environment_is_valid() -> None:
    settings = _production_settings().model_copy(
        update={
            "rate_limit_backend": "redis",
            "redis_url": "rediss://cache.example.com:6379",
        }
    )
    validate_environment(settings)


def test_production_rate_limit_backend_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _production_settings().model_copy(
        update={
            "rate_limit_backend": "redis",
            "redis_url": "rediss://cache.example.com:6379",
        }
    )

    async def unavailable(
        _self: RedisFixedWindowRateLimiter,
        _key: str,
        _now: float,
    ) -> tuple[bool, int]:
        from redis.exceptions import RedisError

        raise RedisError("unavailable")

    monkeypatch.setattr(
        OIDCVerifier,
        "verify",
        lambda _self, _token: Principal("operator-1", Role.operator),
    )
    monkeypatch.setattr(RedisFixedWindowRateLimiter, "allow", unavailable)
    with TestClient(_app(settings)) as client:
        response = client.get("/private", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 503


def test_ssrf_rejects_private_unsafe_and_unlisted_urls() -> None:
    for url in (
        "http://example.com",
        "https://127.0.0.1/admin",
        "https://user:password@example.com",
        "https://localhost/resource",
    ):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url(url, resolve_dns=False)
    with pytest.raises(UnsafeURLError, match="allowlisted"):
        validate_outbound_url(
            "https://example.com",
            allowed_hosts=["identity.example.com"],
            resolve_dns=False,
        )
    assert (
        validate_outbound_url(
            "https://identity.example.com/jwks",
            allowed_hosts=["identity.example.com"],
            resolve_dns=False,
        )
        == "https://identity.example.com/jwks"
    )


def test_secret_references_and_log_redaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TEST_PROVIDER_SECRET", "resolved")
    assert resolve_secret_reference("env://TEST_PROVIDER_SECRET", required=True) == "resolved"
    with pytest.raises(SecretResolutionError):
        resolve_secret_reference(f"file://{tmp_path}/secret", required=True)
    payload = redact(
        {
            "password": "visible",
            "authorization": "Bearer abc.def",
            "email": "adam@example.com",
        }
    )
    assert payload["password"] == "[REDACTED]"
    assert payload["authorization"] == "[REDACTED]"
    assert payload["email"] != "adam@example.com"
    formatter = JSONFormatter(environment="test", service="api", version="abc")
    record = logging.LogRecord("test", logging.INFO, "", 0, "done", (), None)
    rendered = formatter.format(record)
    assert '"environment":"test"' in rendered
    assert '"event":"done"' in rendered


def test_synthetic_incidents_cover_required_alerts_and_runbooks() -> None:
    names = {incident.name for incident in SYNTHETIC_INCIDENTS}
    assert names == {
        "api_unavailable",
        "authentication_anomaly",
        "backup_failure",
        "cost_limit",
        "database_unavailable",
        "deployment_health",
        "lead_routing",
        "queue_age",
        "restore_failure",
        "scan_failures",
        "worker_unavailable",
    }
    root = Path(__file__).parents[2]
    for incident in SYNTHETIC_INCIDENTS:
        assert incident.fires()
        assert (root / incident.runbook).is_file()
    alert_payload = yaml.safe_load(
        (root / "deploy/observability/alerts.yml").read_text()
    )
    alerts = {
        rule["alert"]
        for group in alert_payload["groups"]
        for rule in group["rules"]
    }
    assert {
        "RankRentApiUnavailable",
        "RankRentAuthenticationAnomaly",
        "RankRentBackupFailure",
        "RankRentCostLimitExceeded",
        "RankRentDatabaseUnavailable",
        "RankRentDeploymentHealthFailure",
        "RankRentLeadRoutingFailure",
        "RankRentQueueAgeHigh",
        "RankRentRepeatedScanFailures",
        "RankRentRestoreCheckFailure",
        "RankRentUnexpectedPaidCall",
        "RankRentWorkerUnavailable",
    } <= alerts


def test_audit_rows_are_queryable_in_creation_order() -> None:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        append_audit_event(
            session,
            event_type="auth.login",
            actor=Principal("reviewer", Role.reviewer),
            target_type="session",
            target_id=None,
            request_id="request-2",
        )
        session.commit()
        rows = session.scalars(select(AuditEventORM).order_by(AuditEventORM.id)).all()
    assert [row.event_type for row in rows] == ["auth.login"]


def test_release_manifest_records_required_versions() -> None:
    root = Path(__file__).parents[2]
    manifest = build_manifest(
        root,
        {
            "ENVIRONMENT": "staging",
            "GIT_SHA": "abc123",
            "API_DIGEST": "sha256:api",
            "FRONTEND_DIGEST": "sha256:frontend",
            "RELEASE_NOTES": "Security hardening.",
        },
    )
    assert manifest["migration_version"] == "d4a7c2e9f1b6"
    assert manifest["scoring_version"] == "v2.12"
    assert manifest["evidence_quality_version"] == "v1"
    assert manifest["service_catalog_version"] == "2026.07.1"
    assert manifest["geography_version"] == "us-geography-2024.2"
    assert manifest["prefilter_version"] == "addressable-market-v2.0"
    assert manifest["release_fingerprint"]


def test_release_manifest_verification_rejects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "release.json"
    payload = {
        "environment": "staging",
        "git_sha": "abc123",
        "api_image_digest": "sha256:api",
        "worker_image_digest": "sha256:api",
        "frontend_image_digest": "sha256:frontend",
        "migration_version": "head",
        "scoring_version": "score",
        "evidence_quality_version": "quality",
        "service_catalog_version": "catalog",
        "geography_version": "geography",
        "prefilter_version": "prefilter",
    }
    import hashlib
    import json

    payload["release_fingerprint"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    path.write_text(json.dumps(payload))
    verify_manifest(path, environment="staging", git_sha="abc123")

    payload["scoring_version"] = "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="fingerprint"):
        verify_manifest(path, environment="staging", git_sha="abc123")


def test_dependency_licenses_match_policy() -> None:
    assert denied_licenses(Path(__file__).parents[2]) == []
    assert _is_denied("GNU General Public License v3 (GPLv3)")
    assert _is_denied("AGPL-3.0-only")
    assert not _is_denied("LGPL-3.0-only")
