from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.base import database_readiness, get_session, init_db
from rank_rent.db.orm import (
    ApiCallORM,
    CompetitorMetricORM,
    FullOpportunityScoreORM,
    JsonArtifactORM,
    KeywordClusterORM,
    KeywordDecisionORM,
    KeywordMetricORM,
    MarketPrefilterAssessmentORM,
    MarketPrefilterRunORM,
    OpportunityORM,
    PreliminaryAssessmentORM,
    ProviderCandidateORM,
    ScanPlanCallORM,
    ScanRunORM,
    ScoreComponentORM,
    SerpSnapshotORM,
)
from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    Market,
    OpportunityScore,
    ProviderCandidate,
    SerpSnapshot,
    ServiceFamily,
)
from rank_rent.integrations.dataforseo.live import DataForSEOError
from rank_rent.planning import build_scan_plan
from rank_rent.repositories import get_or_create_opportunity, upsert_market, upsert_service
from rank_rent.runtime import resolve_data_mode, validate_runtime_mode
from rank_rent.scoring.score import OpportunityScorer
from rank_rent.services.data_audit import audit_data
from rank_rent.services.discovery_report import (
    build_api_cost_ledger,
    build_discovery_report,
)
from rank_rent.services.evidence_quality import EvidenceQualityEvaluator
from rank_rent.services.keywords import (
    DEFAULT_AD_HOC_INTENT_MODIFIERS,
    DEFAULT_NEGATIVE_PRODUCT_TERMS,
)
from rank_rent.services.locations import (
    LocationCandidate,
    LocationResolutionError,
    resolve_market_for_scan,
    search_locations,
)
from rank_rent.services.market_prefilter import MarketPrefilter
from rank_rent.services.records import save_scan_plan_calls
from rank_rent.services.scan_worker import active_retry_for_scan
from rank_rent.services.scanner import ScanPipeline
from rank_rent.services.service_catalog import (
    ServiceCatalog,
    ServiceCatalogError,
    ServiceResolution,
    load_service_catalog,
)
from rank_rent.services.us_geography import (
    STATE_NAMES,
    USGeographyError,
)
from rank_rent.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Digital Real Estate Engine", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8010", "http://localhost:8010"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory="src/rank_rent/web/templates")
app.mount("/static", StaticFiles(directory="src/rank_rent/web/static"), name="static")


class ScanRequest(BaseModel):
    service_id: str | None = None
    service_text: str = ""
    location_text: str
    country: str = "US"
    selected_location: LocationCandidate | None = None
    dry_run: bool = False
    async_run: bool = False
    confirm_live_cost: bool = False
    data_mode: str | None = None
    scan_profile: Literal["testing", "full"] | None = None


class MarketPrefilterRequest(BaseModel):
    service_id: str | None = None
    service_text: str = Field(default="", max_length=240)
    states: list[str] = Field(default_factory=list)
    geography_kind: Literal["city", "postal_code"] = "city"
    minimum_population: int | None = Field(default=None, ge=1)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("service_text")
    @classmethod
    def normalize_service_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if normalized and len(normalized) < 2:
            raise ValueError("service_text must contain at least two non-space characters.")
        return normalized

    @field_validator("states")
    @classmethod
    def normalize_states(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().upper() for value in values if value.strip()})
        invalid = [value for value in normalized if value not in STATE_NAMES]
        if invalid:
            raise ValueError(f"Unknown U.S. state abbreviations: {', '.join(invalid)}.")
        return normalized


class RescoreRequest(BaseModel):
    reason: str = Field(
        default="Manual rescore using the current scoring and classification configuration.",
        min_length=3,
        max_length=500,
    )


class PromoteScanRequest(BaseModel):
    dry_run: bool = True
    confirm_live_cost: bool = False


def _service_catalog() -> ServiceCatalog:
    settings = get_settings()
    try:
        return load_service_catalog(
            settings.project_root / "config/services.yaml"
        )
    except ServiceCatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _resolve_service(
    *,
    service_id: str | None,
    service_text: str,
    allow_draft: bool = True,
) -> ServiceResolution:
    catalog = _service_catalog()
    if service_id:
        resolution = catalog.resolve(service_id)
        if resolution is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown configured service id: {service_id}.",
            )
        return resolution
    resolution = catalog.resolve(service_text)
    if resolution is not None:
        return resolution
    if not allow_draft:
        raise HTTPException(
            status_code=422,
            detail="Select a configured service before running a full scan.",
        )
    try:
        draft = catalog.create_draft(service_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Select a configured service or enter a draft service name.",
        ) from exc
    draft.service.negative_terms = ["diy", "jobs", "salary"]
    draft.service.intent_modifiers = list(DEFAULT_AD_HOC_INTENT_MODIFIERS)
    draft.service.negative_product_terms = list(DEFAULT_NEGATIVE_PRODUCT_TERMS)
    return draft


def _ad_hoc_service(service_text: str) -> ServiceFamily:
    return _resolve_service(
        service_id=None,
        service_text=service_text,
    ).service


def _opportunity_summary(row: OpportunityORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "service": row.service_family.display_name,
        "market": row.market.display_name,
        "score": row.latest_score,
        "confidence": row.confidence,
        "status": row.status,
        "missing_data_flags": row.missing_data_flags,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _scan_summary(row: ScanRunORM, session: Session) -> dict[str, Any]:
    typed_counts = _typed_counts(session, row.id)
    return {
        "id": row.id,
        "opportunity_id": row.opportunity_id,
        "source": row.source,
        "status": row.status,
        "data_mode": row.data_mode,
        "scan_profile": row.scan_profile,
        "progress_stage": row.progress_stage,
        "estimated_cost_usd": row.estimated_cost_usd,
        "actual_cost_usd": row.actual_cost_usd,
        "planned_cost_usd": row.planned_cost_usd,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "error_summary": row.error_summary,
        "adapter_names": row.adapter_names,
        "adapter_versions": row.adapter_versions,
        "normalization_version": row.normalization_version,
        "scoring_version": row.scoring_version,
        "cache_policy_version": row.cache_policy_version,
        "source_scan_run_id": row.source_scan_run_id,
        "retry_count": row.retry_count,
        "max_attempts": row.max_attempts,
        "next_attempt_at": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        "cancel_requested": row.cancel_requested,
        "worker_id": row.worker_id,
        "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "quarantined_at": row.quarantined_at.isoformat() if row.quarantined_at else None,
        "quarantine_reason": row.quarantine_reason,
        "integration_versions": row.integration_versions,
        "request_parameters": row.request_parameters,
        "typed_counts": typed_counts,
    }


def _typed_counts(session: Session, scan_run_id: int) -> dict[str, int]:
    return {
        "keyword_metrics": len(
            session.scalars(
                select(KeywordMetricORM.id).where(KeywordMetricORM.scan_run_id == scan_run_id)
            ).all()
        ),
        "keyword_clusters": len(
            session.scalars(
                select(KeywordClusterORM.id).where(KeywordClusterORM.scan_run_id == scan_run_id)
            ).all()
        ),
        "keyword_decisions": len(
            session.scalars(
                select(KeywordDecisionORM.id).where(KeywordDecisionORM.scan_run_id == scan_run_id)
            ).all()
        ),
        "serp_snapshots": len(
            session.scalars(
                select(SerpSnapshotORM.id).where(SerpSnapshotORM.scan_run_id == scan_run_id)
            ).all()
        ),
        "competitor_metrics": len(
            session.scalars(
                select(CompetitorMetricORM.id).where(CompetitorMetricORM.scan_run_id == scan_run_id)
            ).all()
        ),
        "provider_candidates": len(
            session.scalars(
                select(ProviderCandidateORM.id).where(ProviderCandidateORM.scan_run_id == scan_run_id)
            ).all()
        ),
    }


def _scan_plan_call_rows(session: Session, scan_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ScanPlanCallORM)
        .where(ScanPlanCallORM.scan_run_id == scan_id)
        .order_by(ScanPlanCallORM.id)
    ).all()
    return [
        {
            "planned_request_id": row.planned_request_id,
            "provider": row.provider,
            "endpoint": row.endpoint,
            "stage": row.stage,
            "request_parameters": row.request_parameters,
            "cache_key": row.cache_key,
            "cache_hit": row.cache_hit,
            "request_known": row.request_known,
            "estimated_cost_usd": row.estimated_cost_usd,
            "required": row.required,
        }
        for row in rows
    ]


def _api_call_rows(session: Session, scan_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ApiCallORM).where(ApiCallORM.scan_run_id == scan_id).order_by(ApiCallORM.id)
    ).all()
    return [
        {
            "id": row.id,
            "planned_request_id": row.planned_request_id,
            "provider": row.provider,
            "endpoint": row.endpoint,
            "stage": row.stage,
            "cache_hit": row.cache_hit,
            "force_refresh": row.force_refresh,
            "estimated_cost_usd": row.estimated_cost_usd,
            "actual_cost_usd": row.actual_cost_usd,
            "status": row.status,
            "error_type": row.error_type,
            "error_summary": row.error_summary,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "provider_task_id": row.provider_task_id,
            "provider_request_id": row.provider_request_id,
        }
        for row in rows
    ]


def _artifact_data_mode(artifacts: Sequence[JsonArtifactORM]) -> str:
    for artifact in artifacts:
        if artifact.kind in {"scan_result", "preliminary_assessment"}:
            mode = artifact.payload.get("data_mode")
            if isinstance(mode, str):
                return mode
    return validate_runtime_mode(get_settings()).value


def _latest_artifact_payload(
    session: Session, opportunity_id: int, kind: str
) -> dict[str, Any] | None:
    artifact = session.scalar(
        select(JsonArtifactORM)
        .where(JsonArtifactORM.opportunity_id == opportunity_id, JsonArtifactORM.kind == kind)
        .order_by(JsonArtifactORM.id.desc())
        .limit(1)
    )
    return artifact.payload if artifact else None


def _latest_scan_payload(session: Session, opportunity_id: int) -> dict[str, Any] | None:
    artifact = session.scalar(
        select(JsonArtifactORM)
        .where(
            JsonArtifactORM.opportunity_id == opportunity_id,
            JsonArtifactORM.kind.in_(["scan_result", "preliminary_assessment"]),
        )
        .order_by(JsonArtifactORM.id.desc())
        .limit(1)
    )
    return artifact.payload if artifact else None


def _latest_score_artifact(session: Session, opportunity_id: int) -> JsonArtifactORM | None:
    return session.scalar(
        select(JsonArtifactORM)
        .where(
            JsonArtifactORM.opportunity_id == opportunity_id,
            JsonArtifactORM.kind.in_(["scan_result", "preliminary_assessment", "rescore_result"]),
        )
        .order_by(JsonArtifactORM.created_at.desc(), JsonArtifactORM.id.desc())
        .limit(1)
    )


def _latest_report_payload(session: Session, opportunity_id: int) -> dict[str, Any] | None:
    artifact = session.scalar(
        select(JsonArtifactORM)
        .where(
            JsonArtifactORM.opportunity_id == opportunity_id,
            JsonArtifactORM.kind.in_(["discovery_report", "rescore_result"]),
        )
        .order_by(JsonArtifactORM.created_at.desc(), JsonArtifactORM.id.desc())
        .limit(1)
    )
    if artifact is None:
        return None
    if artifact.kind == "rescore_result" and isinstance(artifact.payload.get("discovery_report"), dict):
        return cast(dict[str, Any], artifact.payload["discovery_report"])
    return artifact.payload


def _latest_score_payload(session: Session, opportunity_id: int) -> dict[str, Any] | None:
    artifact = _latest_score_artifact(session, opportunity_id)
    if artifact is None:
        return None
    if isinstance(artifact.payload.get("score"), dict):
        score = cast(dict[str, Any], artifact.payload["score"])
        return {
            **score,
            "assessment_type": artifact.payload.get("assessment_type"),
            "artifact_kind": artifact.kind,
            "artifact_created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        }
    return None


def _score_history(session: Session, opportunity_id: int) -> list[dict[str, Any]]:
    artifacts = session.scalars(
        select(JsonArtifactORM)
        .where(
            JsonArtifactORM.opportunity_id == opportunity_id,
            JsonArtifactORM.kind.in_(
                ["scan_result", "preliminary_assessment", "rescore_result"]
            ),
        )
        .order_by(JsonArtifactORM.created_at.desc(), JsonArtifactORM.id.desc())
    ).all()
    history: list[dict[str, Any]] = []
    for artifact in artifacts:
        score = artifact.payload.get("score")
        if not isinstance(score, dict):
            continue
        history.append(
            {
                "artifact_id": artifact.id,
                "artifact_kind": artifact.kind,
                "created_at": artifact.created_at.isoformat()
                if artifact.created_at
                else None,
                "assessment_type": artifact.payload.get("assessment_type"),
                "source_scan_run_id": artifact.payload.get("source_scan_run_id"),
                "reason": artifact.payload.get("reason"),
                "diff": artifact.payload.get("diff"),
                "score": score,
            }
        )
    return history


def _score_diff(
    previous: dict[str, Any] | None,
    current: OpportunityScore,
) -> dict[str, Any]:
    previous_total = (
        float(previous["total_score"])
        if previous and isinstance(previous.get("total_score"), (int, float))
        else None
    )
    previous_components_raw = previous.get("component_scores") if previous else None
    previous_components: dict[str, Any] = (
        previous_components_raw
        if isinstance(previous_components_raw, dict)
        else {}
    )
    component_deltas = {
        component: round(
            value - float(previous_components.get(component, 0)),
            4,
        )
        for component, value in current.component_scores.items()
    }
    return {
        "previous_total_score": previous_total,
        "new_total_score": current.total_score,
        "total_delta": (
            round(current.total_score - previous_total, 4)
            if previous_total is not None
            else None
        ),
        "component_deltas": component_deltas,
        "previous_scoring_version": previous.get("scoring_version")
        if previous
        else None,
        "new_scoring_version": current.scoring_version,
    }


def _latest_assessment(
    session: Session,
    opportunity_id: int,
) -> dict[str, Any] | None:
    artifact = _latest_score_artifact(session, opportunity_id)
    if artifact is None:
        return None
    score = artifact.payload.get("score")
    if not isinstance(score, dict):
        return None
    report = artifact.payload.get("discovery_report")
    if not isinstance(report, dict):
        report = _latest_report_payload(session, opportunity_id)
    report = report if isinstance(report, dict) else {}
    scan_metadata_raw = report.get("scan_metadata")
    scan_metadata: dict[str, Any] = (
        scan_metadata_raw if isinstance(scan_metadata_raw, dict) else {}
    )
    scan_id = artifact.payload.get("source_scan_run_id") or scan_metadata.get(
        "scan_run_id"
    )
    scan = (
        session.get(ScanRunORM, int(scan_id))
        if isinstance(scan_id, (int, str)) and str(scan_id).isdigit()
        else None
    )
    assessment_type = str(
        artifact.payload.get("assessment_type")
        or scan_metadata.get("assessment_type")
        or ("preliminary" if artifact.kind == "preliminary_assessment" else "full")
    )
    evidence_status = str(score.get("evidence_status") or "complete")
    artifact_quality = artifact.payload.get("evidence_quality")
    report_quality = report.get("evidence_quality")
    quality: dict[str, Any] = (
        artifact_quality
        if isinstance(artifact_quality, dict)
        else report_quality
        if isinstance(report_quality, dict)
        else {}
    )
    rankable = (
        assessment_type == "full"
        and evidence_status == "complete"
        and quality.get("status") != "fail"
    )
    return {
        "assessment_type": assessment_type,
        "rankable": rankable,
        "score": score,
        "report": report,
        "evidence_quality": quality,
        "freshness": report.get("data_freshness"),
        "scan": _scan_summary(scan, session) if scan else None,
        "cost_ledger": build_api_cost_ledger(session, scan.id) if scan else None,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "artifact_kind": artifact.kind,
    }


def _promotion_status(
    session: Session,
    opportunity_id: int,
    assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    active = session.scalar(
        select(ScanRunORM)
        .where(
            ScanRunORM.opportunity_id == opportunity_id,
            ScanRunORM.status.in_(["queued", "running"]),
        )
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    )
    scan = assessment.get("scan") if assessment else None
    quality = assessment.get("evidence_quality") if assessment else {}
    already_full = bool(scan and scan.get("scan_profile") == "full")
    eligible = bool(
        scan
        and scan.get("status") == "completed"
        and scan.get("data_mode") == "live"
        and scan.get("scan_profile") == "testing"
        and not active
        and (not isinstance(quality, dict) or quality.get("status") != "fail")
    )
    reason = (
        None
        if eligible
        else f"Scan {active.id} is already active."
        if active
        else "The latest assessment already uses the full scan profile."
        if already_full
        else "Resolve evidence-quality errors before promoting this testing assessment."
        if isinstance(quality, dict) and quality.get("status") == "fail"
        else "A completed live testing assessment is required."
    )
    return {
        "eligible": eligible,
        "reason": reason,
        "source_scan_run_id": scan.get("id") if scan else None,
        "active_scan_id": active.id if active else None,
        "already_full": already_full,
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str | bool | None]:
    status = database_readiness()
    if not status["ready"]:
        raise HTTPException(status_code=503, detail=status)
    return status


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    settings = get_settings()
    data_mode = validate_runtime_mode(settings)
    try:
        prefilter = MarketPrefilter.from_settings(settings)
        geography_metadata = prefilter.index.metadata()
        geography_status: dict[str, Any] = {
            "mode": "offline_us_geography",
            "dataset_available": True,
            "dataset_version": geography_metadata.get("dataset_version"),
            "reference_year": geography_metadata.get("reference_year"),
            "city_count": int(geography_metadata.get("city_count", 0)),
            "zip_count": int(geography_metadata.get("zip_count", 0)),
            "public_data_signals": [
                "households",
                "housing_units",
                "owner_occupied_units",
                "median_year_built",
            ],
            "complete_housing_signal_count": int(
                geography_metadata.get("complete_housing_signal_count", 0)
            ),
            "market_prefilter_version": prefilter.config.version,
        }
    except (USGeographyError, OSError, ValueError):
        geography_status = {
            "mode": "offline_us_geography",
            "dataset_available": False,
        }
    return {
        "data_mode": data_mode.value,
        "synthetic_fixture_data": data_mode.value == "fixture",
        "live_api_calls_allowed": settings.allow_live_api_calls,
        "live_scan_depth": settings.live_scan_depth,
        "dataforseo_environment": settings.dataforseo_environment,
        "dataforseo_sandbox": settings.dataforseo_environment.strip().lower() == "sandbox",
        "requires_live_cost_confirmation": data_mode.value == "live",
        "geocoder": geography_status,
    }


@app.get("/api/locations/search")
async def api_location_search(
    q: str,
    country: str = "US",
    limit: int = 8,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        candidates = await search_locations(
            session=session,
            query=q,
            country=country,
            settings=settings,
            limit=max(1, min(limit, 12)),
        )
    except USGeographyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"locations": [candidate.model_dump(mode="json") for candidate in candidates]}


@app.get("/api/services")
def api_services(q: str = "", limit: int = 50) -> dict[str, Any]:
    catalog = _service_catalog()
    records = catalog.search(q, limit=max(1, min(limit, 100)))
    return {
        "catalog_version": catalog.version,
        "services": [
            {
                **record.service.model_dump(mode="json"),
                "aliases": record.aliases,
                "configured": record.configured,
            }
            for record in records
        ],
    }


@app.post("/api/market-prefilter")
def api_market_prefilter(
    payload: MarketPrefilterRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings()
    service_resolution = _resolve_service(
        service_id=payload.service_id,
        service_text=payload.service_text,
    )
    service = service_resolution.service
    prefilter = MarketPrefilter.from_settings(settings)
    assessments, candidate_count = prefilter.rank_markets(
        service,
        states=payload.states,
        geography_kind=payload.geography_kind,
        limit=payload.limit,
        minimum_population=payload.minimum_population,
    )
    metadata = prefilter.index.metadata()
    profile = (
        assessments[0].service_profile
        if assessments
        else prefilter.profile_for_service(service)[0]
    )
    run = MarketPrefilterRunORM(
        service_text=service.display_name,
        service_profile=profile,
        geography_kind=payload.geography_kind,
        state_filters=sorted({state.strip().upper() for state in payload.states}),
        minimum_population=(
            payload.minimum_population
            if payload.minimum_population is not None
            else prefilter.config.minimum_population
        ),
        candidate_count=candidate_count,
        returned_count=len(assessments),
        assessment_version=prefilter.config.version,
        config_hash=prefilter.config_hash,
        geography_dataset_version=str(metadata.get("dataset_version") or "unknown"),
        status="completed",
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            MarketPrefilterAssessmentORM(
                prefilter_run_id=run.id,
                geography_id=assessment.location.geography_id,
                rank=assessment.rank or 0,
                score=assessment.score,
                recommendation=assessment.recommendation,
                confidence=assessment.confidence,
                payload=assessment.model_dump(mode="json"),
            )
            for assessment in assessments
        ]
    )
    session.commit()
    return {
        "prefilter_run_id": run.id,
        "service": service.display_name,
        "service_resolution": service_resolution.model_dump(mode="json"),
        "zero_cost": True,
        "paid_api_calls": 0,
        "candidate_count": candidate_count,
        "returned_count": len(assessments),
        "assessment_version": run.assessment_version,
        "config_hash": run.config_hash,
        "geography_dataset_version": run.geography_dataset_version,
        "service_profile": profile,
        "assessments": [
            assessment.model_dump(mode="json") for assessment in assessments
        ],
    }


@app.get("/api/opportunities")
def api_opportunities(session: Session = Depends(get_session)) -> dict[str, Any]:
    settings = get_settings()
    data_mode = validate_runtime_mode(settings)
    opportunities = session.scalars(select(OpportunityORM).order_by(OpportunityORM.id.desc())).all()
    return {
        "data_mode": data_mode.value,
        "synthetic_fixture_data": data_mode.value == "fixture",
        "live_scan_depth": settings.live_scan_depth,
        "dataforseo_environment": settings.dataforseo_environment,
        "opportunities": [
            {
                **_opportunity_summary(row),
                "latest_assessment": _latest_assessment(session, row.id),
            }
            for row in opportunities
        ],
    }


@app.get("/api/opportunities/compare")
def api_opportunity_compare(ids: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    opportunity_ids = [int(item.strip()) for item in ids.split(",") if item.strip().isdigit()]
    if not opportunity_ids:
        raise HTTPException(status_code=400, detail="Provide one or more numeric opportunity ids.")
    rows = session.scalars(
        select(OpportunityORM).where(OpportunityORM.id.in_(opportunity_ids)).order_by(OpportunityORM.id)
    ).all()
    items = []
    rejected = []
    for row in rows:
        assessment = _latest_assessment(session, row.id)
        item = {
            "opportunity": _opportunity_summary(row),
            "latest_report": _latest_report_payload(session, row.id),
            "latest_score": _latest_score_payload(session, row.id),
            "latest_assessment": assessment,
        }
        if assessment is None or not assessment["rankable"]:
            rejected.append(
                {
                    "opportunity_id": row.id,
                    "reason": "Only complete full assessments can be compared.",
                }
            )
        else:
            items.append(item)
    if rejected:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Comparison accepts complete full assessments only.",
                "rejected": rejected,
            },
        )
    return {"opportunities": items}


@app.get("/api/opportunities/{opportunity_id}")
def api_opportunity_detail(
    opportunity_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    opportunity = session.get(OpportunityORM, opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    artifacts = session.scalars(
        select(JsonArtifactORM)
        .where(JsonArtifactORM.opportunity_id == opportunity_id)
        .order_by(JsonArtifactORM.id.desc())
    ).all()
    latest_scan = session.scalar(
        select(ScanRunORM)
        .where(ScanRunORM.opportunity_id == opportunity_id)
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    )
    latest_assessment = _latest_assessment(session, opportunity_id)
    return {
        "data_mode": _artifact_data_mode(artifacts),
        "opportunity": _opportunity_summary(opportunity),
        "keyword_decisions": _keyword_decision_rows(session, latest_scan.id) if latest_scan else [],
        "keyword_clusters": _keyword_cluster_rows(session, latest_scan.id) if latest_scan else [],
        "latest_scan": _scan_summary(latest_scan, session) if latest_scan else None,
        "api_calls": _api_call_rows(session, latest_scan.id) if latest_scan else [],
        "score_history": _score_history(session, opportunity_id),
        "latest_assessment": latest_assessment,
        "promotion": _promotion_status(
            session,
            opportunity_id,
            latest_assessment,
        ),
        "artifacts": [
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "payload": artifact.payload,
                "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
            }
            for artifact in artifacts
        ],
    }


@app.post("/api/opportunities/{opportunity_id}/rescore")
def api_opportunity_rescore(
    opportunity_id: int,
    payload: RescoreRequest | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    opportunity = session.get(OpportunityORM, opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    artifact = _latest_scan_payload(session, opportunity_id)
    latest_scan = session.scalar(
        select(ScanRunORM)
        .where(ScanRunORM.opportunity_id == opportunity_id)
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    )
    if artifact is None or latest_scan is None:
        raise HTTPException(status_code=400, detail="No stored scan evidence is available to rescore.")
    request = latest_scan.request_parameters or {}
    service_payload = request.get("service_payload")
    market_payload = request.get("final_market_payload") or request.get("market_payload")
    if not isinstance(service_payload, dict) or not isinstance(market_payload, dict):
        raise HTTPException(status_code=400, detail="Latest scan is missing structured service/market data.")
    service = ServiceFamily(**service_payload)
    market = Market(**market_payload)
    metrics = [KeywordMetric(**item) for item in artifact.get("metrics", []) if isinstance(item, dict)]
    serp_snapshots = [
        SerpSnapshot(**item) for item in artifact.get("serp_snapshots", []) if isinstance(item, dict)
    ]
    competitors = [
        CompetitorMetric(**item) for item in artifact.get("competitors", []) if isinstance(item, dict)
    ]
    providers = [
        ProviderCandidate(**item) for item in artifact.get("providers", []) if isinstance(item, dict)
    ]
    assessment_type = _assessment_type_for_payload(artifact, latest_scan)
    is_preliminary = assessment_type == "preliminary"
    evidence_source_mode = _scan_evidence_source_mode(latest_scan)
    previous_score = _latest_score_payload(session, opportunity_id)
    scorer = OpportunityScorer()
    score = scorer.score(
        metrics,
        serp_snapshots,
        competitors,
        providers,
        market,
        source_mode=evidence_source_mode,
        assessment_type=assessment_type,
    )
    quality_evaluator = EvidenceQualityEvaluator(
        get_settings().project_root / "config/evidence_quality.yaml"
    )
    evidence_quality = quality_evaluator.assess(
        service=service,
        metrics=metrics,
        serp_snapshots=serp_snapshots,
        competitors=competitors,
        providers=providers,
        assessment_type=assessment_type,
    )
    score = quality_evaluator.apply_to_score(score, evidence_quality)
    score_diff = _score_diff(previous_score, score)
    reason = (
        payload.reason
        if payload is not None
        else "Manual rescore using the current scoring and classification configuration."
    )
    cost_ledger = build_api_cost_ledger(session, latest_scan.id)
    report = build_discovery_report(
        service=service,
        market=market,
        metrics=metrics,
        serp_snapshots=serp_snapshots,
        competitors=competitors,
        providers=providers,
        score=score,
        scan_metadata={
            "scan_run_id": latest_scan.id,
            "rescored_from_stored_data": True,
            "data_mode": latest_scan.data_mode,
            "evidence_source_mode": evidence_source_mode,
            "scan_profile": latest_scan.scan_profile,
            "planned_cost_usd": latest_scan.planned_cost_usd,
            "actual_cost_usd": cost_ledger["actual_cost_usd"],
            "completed_at": latest_scan.completed_at.isoformat()
            if latest_scan.completed_at
            else None,
            "api_cost_ledger": cost_ledger,
            "assessment_type": assessment_type,
            "evidence_quality": evidence_quality.model_dump(mode="json"),
        },
        demand_estimator=scorer.market_demand_estimator,
        public_data_prefilter=(
            artifact.get("public_data_prefilter")
            if isinstance(artifact.get("public_data_prefilter"), dict)
            else None
        ),
        evidence_quality=evidence_quality.model_dump(mode="json"),
    )
    _save_rescore_assessment_records(
        session,
        scan=latest_scan,
        opportunity_id=opportunity_id,
        score=score,
        is_preliminary=is_preliminary,
    )
    if is_preliminary:
        if opportunity.latest_score is None:
            opportunity.status = (
                "evidence_rejected"
                if evidence_quality.status == "fail"
                else "preliminary_review"
            )
            opportunity.score_version = score.scoring_version
            opportunity.confidence = (
                "insufficient"
                if evidence_quality.status == "fail"
                else "preliminary"
            )
            opportunity.missing_data_flags = score.missing_fields
    elif score.evidence_status == "complete":
        opportunity.status = "full_review"
        opportunity.latest_score = score.total_score
        opportunity.score_version = score.scoring_version
        opportunity.confidence = score.confidence.value
        opportunity.missing_data_flags = score.missing_fields
    else:
        opportunity.status = f"{score.evidence_status}_review"
        if opportunity.latest_score is None:
            opportunity.score_version = score.scoring_version
            opportunity.confidence = score.confidence.value
        opportunity.missing_data_flags = score.missing_fields
    session.add(
        JsonArtifactORM(
            opportunity_id=opportunity_id,
            scan_run_id=latest_scan.id,
            kind="rescore_result",
            payload={
                "assessment_type": assessment_type,
                "score": score.model_dump(mode="json"),
                "discovery_report": report,
                "source_scan_run_id": latest_scan.id,
                "reason": reason,
                "diff": score_diff,
                "evidence_quality": evidence_quality.model_dump(mode="json"),
            },
        )
    )
    session.commit()
    return {
        "rescored": True,
        "assessment_type": assessment_type,
        "opportunity": _opportunity_summary(opportunity),
        "score": score.model_dump(mode="json"),
        "reason": reason,
        "diff": score_diff,
        "evidence_quality": evidence_quality.model_dump(mode="json"),
        "discovery_report": report,
    }


@app.post("/api/opportunities/{opportunity_id}/promote")
def api_opportunity_promote(
    opportunity_id: int,
    payload: PromoteScanRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    opportunity = session.get(OpportunityORM, opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    active_scan = session.scalar(
        select(ScanRunORM)
        .where(
            ScanRunORM.opportunity_id == opportunity_id,
            ScanRunORM.status.in_(["queued", "running"]),
        )
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    )
    if active_scan is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Scan {active_scan.id} is already active for this opportunity.",
        )
    source_scan = session.scalar(
        select(ScanRunORM)
        .where(
            ScanRunORM.opportunity_id == opportunity_id,
            ScanRunORM.status == "completed",
            ScanRunORM.scan_profile == "testing",
        )
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    )
    if source_scan is None:
        completed_full = session.scalar(
            select(ScanRunORM.id)
            .where(
                ScanRunORM.opportunity_id == opportunity_id,
                ScanRunORM.status == "completed",
                ScanRunORM.scan_profile == "full",
            )
            .limit(1)
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "This opportunity already has a completed full assessment."
                if completed_full is not None
                else "No completed testing scan is available to promote."
            ),
        )
    source_quality = (source_scan.partial_outputs or {}).get("evidence_quality")
    if isinstance(source_quality, dict) and source_quality.get("status") == "fail":
        raise HTTPException(
            status_code=409,
            detail=(
                "Resolve evidence-quality errors before promoting this testing "
                "assessment to a full scan."
            ),
        )
    request = source_scan.request_parameters or {}
    service_payload = request.get("service_payload")
    market_payload = request.get("final_market_payload") or request.get(
        "market_payload"
    )
    if not isinstance(service_payload, dict) or not isinstance(market_payload, dict):
        raise HTTPException(
            status_code=400,
            detail="The source scan is missing structured service or market metadata.",
        )
    data_mode = resolve_data_mode(str(request.get("data_mode") or source_scan.data_mode))
    if data_mode.value != "live":
        raise HTTPException(
            status_code=400,
            detail="Only a live testing assessment can be promoted to a full scan.",
        )
    settings = get_settings()
    validate_runtime_mode(settings, data_mode)
    source_service = ServiceFamily(**service_payload)
    service_resolution = _service_catalog().resolve(source_service.id)
    if service_resolution is None:
        service_resolution = _service_catalog().resolve(
            source_service.display_name
        )
    if service_resolution is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "This opportunity uses an unconfigured draft service. Select or "
                "create an authoritative service definition before a full scan."
            ),
        )
    service = service_resolution.service
    market = Market(**market_payload)
    plan = build_scan_plan(
        settings,
        data_mode,
        service,
        market,
        session=session,
        scan_profile="full",
    )
    if plan.blocked:
        raise HTTPException(
            status_code=400,
            detail=plan.block_reason or "Full scan blocked by cost policy.",
        )
    response = {
        "promotion": True,
        "dry_run": payload.dry_run,
        "source_scan_run_id": source_scan.id,
        "opportunity_id": opportunity_id,
        "scan_plan": plan.model_dump(mode="json"),
        "additional_uncached_call_count": sum(
            not call.cache_hit for call in plan.planned_calls
        ),
        "additional_estimated_cost_usd": float(plan.estimated_uncached_cost_usd),
    }
    if payload.dry_run:
        return response
    if (
        plan.estimated_uncached_cost_usd > 0
        and not payload.confirm_live_cost
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Full scan promotion requires explicit cost confirmation. "
                f"Estimated uncached cost: ${plan.estimated_uncached_cost_usd}."
            ),
        )
    queued = _queue_scan(
        session,
        service,
        market,
        data_mode.value,
        plan.model_dump(mode="json"),
        source_scan_run_id=source_scan.id,
        public_data_prefilter=(
            request.get("public_data_prefilter")
            if isinstance(request.get("public_data_prefilter"), dict)
            else None
        ),
        source="promotion_async",
    )
    return {
        **response,
        "dry_run": False,
        "queued": True,
        "scan_id": queued.id,
        "message": (
            f"Queued full scan {queued.id} from preliminary scan {source_scan.id}."
        ),
    }


def _assessment_type_for_payload(payload: dict[str, Any], scan: ScanRunORM) -> str:
    explicit = payload.get("assessment_type")
    if explicit in {"preliminary", "full"}:
        return str(explicit)
    if scan.data_mode == "live" and scan.scan_profile == "testing":
        return "preliminary"
    return "full"


def _scan_evidence_source_mode(scan: ScanRunORM) -> str:
    request = scan.request_parameters or {}
    integrations = scan.integration_versions or {}
    explicit = request.get("evidence_source_mode") or integrations.get(
        "evidence_source_mode"
    )
    if isinstance(explicit, str) and explicit:
        return explicit
    if scan.data_mode in {"fixture", "replay"}:
        return str(scan.data_mode)
    environment = request.get("dataforseo_environment") or integrations.get(
        "dataforseo_environment"
    )
    if environment == "production":
        return "live"
    if environment == "sandbox":
        return "sandbox"
    return "unknown"


def _save_rescore_assessment_records(
    session: Session,
    *,
    scan: ScanRunORM,
    opportunity_id: int,
    score: OpportunityScore,
    is_preliminary: bool,
) -> None:
    payload = {
        **score.model_dump(mode="json"),
        "rescore": True,
        "source_scan_run_id": scan.id,
    }
    if is_preliminary:
        session.add(
            PreliminaryAssessmentORM(
                scan_run_id=scan.id,
                opportunity_id=opportunity_id,
                scoring_version=score.scoring_version,
                confidence="preliminary",
                missing_components=score.missing_fields,
                payload=payload,
            )
        )
    else:
        session.add(
            FullOpportunityScoreORM(
                scan_run_id=scan.id,
                opportunity_id=opportunity_id,
                scoring_version=score.scoring_version,
                total_score=score.total_score,
                confidence=score.confidence.value,
                explanation=score.explanation,
                payload=payload,
            )
        )
    for component, value in score.component_scores.items():
        detail = score.component_details.get(component)
        session.add(
            ScoreComponentORM(
                scan_run_id=scan.id,
                component=component,
                score=value,
                inputs={
                    "measurements": detail.inputs,
                    "calculation_steps": [
                        step.model_dump(mode="json")
                        for step in detail.calculation_steps
                    ],
                    "maximum_score": detail.maximum_score,
                    "explanation": detail.explanation,
                    "rescore": True,
                }
                if detail
                else {"rescore": True},
                formula=detail.formula if detail else "",
                penalties={},
            )
        )


def _keyword_decision_rows(session: Session, scan_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(KeywordDecisionORM)
        .where(KeywordDecisionORM.scan_run_id == scan_id)
        .order_by(KeywordDecisionORM.representative.desc(), KeywordDecisionORM.rank, KeywordDecisionORM.id)
    ).all()
    return [
        {
            "keyword": row.keyword,
            "canonical_keyword": row.canonical_keyword,
            "decision": row.decision,
            "reason": row.reason,
            "rank": row.rank,
            "representative": row.representative,
            "cluster_id": row.cluster_id,
            "intent": row.intent,
            "search_volume": row.search_volume,
            "cpc": row.cpc,
            "granularity": row.granularity,
            "ranking_score": row.ranking_score,
        }
        for row in rows
    ]


def _keyword_cluster_rows(session: Session, scan_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(KeywordClusterORM)
        .where(KeywordClusterORM.scan_run_id == scan_id)
        .order_by(KeywordClusterORM.id)
    ).all()
    return [
        {
            "representative_keyword": row.representative_keyword,
            "keywords": row.keywords,
            "dedupe_method": row.dedupe_method,
            "combined_volume": row.combined_volume,
        }
        for row in rows
    ]


@app.get("/api/data/audit")
def api_data_audit(session: Session = Depends(get_session)) -> dict[str, Any]:
    return audit_data(session)


@app.get("/api/scans")
def api_scans(session: Session = Depends(get_session)) -> dict[str, Any]:
    scans = session.scalars(select(ScanRunORM).order_by(ScanRunORM.id.desc()).limit(50)).all()
    return {"scans": [_scan_summary(row, session) for row in scans]}


@app.get("/api/scans/{scan_id}")
def api_scan_status(scan_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    scan = session.get(ScanRunORM, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return {
        "scan": _scan_summary(scan, session),
        "planned_calls": _scan_plan_call_rows(session, scan_id),
    }


@app.post("/api/scans/{scan_id}/cancel")
def api_cancel_scan(scan_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    scan = session.get(ScanRunORM, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.status in {"completed", "failed", "cancelled"}:
        return {
            "cancelled": scan.status == "cancelled",
            "message": f"Scan {scan.id} is already {scan.status}.",
            "scan": _scan_summary(scan, session),
        }
    scan.cancel_requested = True
    if scan.status == "queued":
        scan.status = "cancelled"
        scan.progress_stage = "cancelled"
        scan.completed_at = datetime.now(UTC)
        scan.worker_id = None
        scan.heartbeat_at = None
        scan.lease_token = None
        scan.lease_expires_at = None
    session.commit()
    return {
        "cancelled": True,
        "message": f"Cancellation requested for scan {scan.id}.",
        "scan": _scan_summary(scan, session),
    }


@app.post("/api/scans/{scan_id}/retry")
def api_retry_scan(
    scan_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    scan = session.get(ScanRunORM, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Active scans cannot be retried.")
    existing_retry = active_retry_for_scan(session, scan.id)
    if existing_retry is not None:
        return {
            "queued": True,
            "message": f"Retry scan {existing_retry.id} is already active.",
            "scan_id": existing_retry.id,
            "source_scan_run_id": scan.id,
            "scan_plan": (existing_retry.request_parameters or {}).get("scan_plan"),
        }
    request = scan.request_parameters or {}
    service_payload = request.get("service_payload")
    market_payload = request.get("market_payload")
    if not isinstance(service_payload, dict) or not isinstance(market_payload, dict):
        raise HTTPException(
            status_code=400,
            detail="This scan does not have structured retry metadata. Run a new scan instead.",
        )
    data_mode = str(request.get("data_mode") or scan.data_mode)
    scan_profile = str(request.get("scan_profile") or scan.scan_profile)
    service = ServiceFamily(**service_payload)
    market = Market(**market_payload)
    plan = build_scan_plan(
        get_settings(),
        resolve_data_mode(data_mode),
        service,
        market,
        session=session,
        scan_profile=scan_profile,
    )
    if plan.blocked:
        raise HTTPException(status_code=400, detail=plan.block_reason or "Retry blocked by cost policy.")
    retry = _queue_scan(
        session,
        service,
        market,
        data_mode,
        plan.model_dump(mode="json"),
        source_scan_run_id=scan.id,
        retry_count=(scan.retry_count or 0) + 1,
    )
    return {
        "queued": True,
        "message": f"Queued retry scan {retry.id} from scan {scan.id}.",
        "scan_id": retry.id,
        "source_scan_run_id": scan.id,
        "scan_plan": plan.model_dump(mode="json"),
    }


@app.post("/api/scans")
async def api_scan(
    payload: ScanRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings()
    data_mode = validate_runtime_mode(settings)
    requested_mode = resolve_data_mode(payload.data_mode or data_mode)
    validate_runtime_mode(settings, requested_mode)
    requested_profile = payload.scan_profile or settings.live_scan_depth
    service_resolution = _resolve_service(
        service_id=payload.service_id,
        service_text=payload.service_text,
        allow_draft=requested_profile != "full",
    )
    service = service_resolution.service
    try:
        market = await resolve_market_for_scan(
            session=session,
            location_text=payload.location_text,
            country=payload.country,
            settings=settings,
            selected_location=payload.selected_location,
        )
    except LocationResolutionError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "candidates": [candidate.model_dump(mode="json") for candidate in exc.candidates],
            },
        ) from exc
    prefilter_assessment = MarketPrefilter.from_settings(settings).assess_market(
        service,
        market,
    )
    if payload.dry_run:
        plan = build_scan_plan(
            settings,
            requested_mode,
            service,
            market,
            session=session,
            scan_profile=requested_profile,
        )
        return {
            "dry_run": True,
            "data_mode": requested_mode.value,
            "synthetic_fixture_data": requested_mode.value == "fixture",
            "scan_plan": plan.model_dump(mode="json"),
            "message": (
                f"Interpreted as {service.display_name} in {market.display_name}. "
                f"Estimated uncached cost: ${plan.estimated_uncached_cost_usd}."
            ),
            "resolved": {
                "service": service.display_name,
                "service_resolution": service_resolution.model_dump(mode="json"),
                "market": market.model_dump(mode="json"),
            },
            "public_data_prefilter": (
                prefilter_assessment.model_dump(mode="json")
                if prefilter_assessment
                else None
            ),
        }
    plan = build_scan_plan(
        settings,
        requested_mode,
        service,
        market,
        session=session,
        scan_profile=requested_profile,
    )
    if plan.blocked:
        raise HTTPException(status_code=400, detail=plan.block_reason or "Scan blocked by cost policy.")
    if (
        requested_mode.value == "live"
        and plan.estimated_uncached_cost_usd > 0
        and not payload.confirm_live_cost
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Live scan requires explicit cost confirmation. "
                f"Estimated uncached cost: ${plan.estimated_uncached_cost_usd}."
            ),
        )
    if payload.async_run:
        scan = _queue_scan(
            session,
            service,
            market,
            requested_mode.value,
            plan.model_dump(mode="json"),
            public_data_prefilter=(
                prefilter_assessment.model_dump(mode="json")
                if prefilter_assessment
                else None
            ),
        )
        return {
            "dry_run": False,
            "queued": True,
            "data_mode": requested_mode.value,
            "synthetic_fixture_data": requested_mode.value == "fixture",
            "assessment_type": "pending",
            "service_resolution": service_resolution.model_dump(mode="json"),
            "scan_plan": plan.model_dump(mode="json"),
            "message": f"Queued scan {scan.id}.",
            "scan_id": scan.id,
            "opportunity_id": scan.opportunity_id,
        }
    try:
        result = await ScanPipeline(
            session,
            data_mode=requested_mode,
            scan_profile=requested_profile,
        ).run(
            service,
            market,
            source="manual",
        )
    except DataForSEOError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "dry_run": False,
        "data_mode": result["data_mode"],
        "synthetic_fixture_data": result["data_mode"] == "fixture",
        "assessment_type": result["assessment_type"],
        "service_resolution": service_resolution.model_dump(mode="json"),
        "scan_plan": result["scan_plan"].model_dump(mode="json"),
        "message": (
            f"Created opportunity {result['opportunity_id']} "
            f"with {'a preliminary assessment' if result['assessment_type'] == 'preliminary' else 'score ' + str(result['score'].total_score)}."
        ),
        "opportunity_id": result["opportunity_id"],
        "scan_id": result["scan_id"],
        "score": result["score"].model_dump(mode="json"),
        "site_path": str(result["site_path"]) if result["site_path"] else None,
    }


def _queue_scan(
    session: Session,
    service: ServiceFamily,
    market: Market,
    data_mode: str,
    scan_plan: dict[str, Any],
    source_scan_run_id: int | None = None,
    retry_count: int = 0,
    public_data_prefilter: dict[str, Any] | None = None,
    source: str = "manual_async",
) -> ScanRunORM:
    service_row = upsert_service(session, service)
    market_row = upsert_market(session, market)
    opportunity = get_or_create_opportunity(session, service_row, market_row)
    scan = ScanRunORM(
        opportunity_id=opportunity.id,
        source=source,
        status="queued",
        estimated_cost_usd=float(scan_plan.get("estimated_uncached_cost_usd") or 0),
        planned_cost_usd=float(scan_plan.get("estimated_uncached_cost_usd") or 0),
        data_mode=data_mode,
        scan_profile=str(scan_plan.get("scan_profile") or "testing"),
        source_scan_run_id=source_scan_run_id,
        retry_count=retry_count,
        max_attempts=get_settings().scan_worker_max_attempts,
        progress_stage="queued",
        cache_policy_version="v2",
        integration_versions={
            "data_mode": data_mode,
            "queued": True,
            "async_worker": "separate_process",
        },
        request_parameters={
            "service": service.slug,
            "market": market.slug,
            "data_mode": data_mode,
            "scan_profile": str(scan_plan.get("scan_profile") or "testing"),
            "scan_plan": scan_plan,
            "service_payload": service.model_dump(mode="json"),
            "market_payload": market.model_dump(mode="json"),
            "final_market_payload": market.model_dump(mode="json"),
            "public_data_prefilter": public_data_prefilter,
        },
    )
    session.add(scan)
    session.flush()
    plan = build_scan_plan(
        get_settings(),
        resolve_data_mode(data_mode),
        service,
        market,
        session=session,
        scan_profile=str(scan_plan.get("scan_profile") or "testing"),
    )
    save_scan_plan_calls(session, scan.id, plan)
    session.commit()
    return scan


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    opportunities = session.scalars(select(OpportunityORM).order_by(OpportunityORM.id.desc())).all()
    return templates.TemplateResponse(
        request, "dashboard.html", {"opportunities": opportunities}
    )


@app.post("/scan", response_class=HTMLResponse)
async def scan(
    request: Request,
    service_text: str = Form(...),
    location_text: str = Form(...),
    country: str = Form("US"),
    dry_run: bool = Form(False),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()
    data_mode = validate_runtime_mode(settings)
    service = _ad_hoc_service(service_text)
    try:
        market = await resolve_market_for_scan(
            session=session,
            location_text=location_text,
            country=country,
            settings=settings,
        )
    except LocationResolutionError as exc:
        return templates.TemplateResponse(
            request,
            "scan_result.html",
            {"dry_run": False, "message": f"Scan failed: {exc}"},
        )
    plan = build_scan_plan(settings, data_mode, service, market, session=session)
    if dry_run:
        return templates.TemplateResponse(
            request,
            "scan_result.html",
            {
                "dry_run": True,
                "message": (
                    f"Interpreted as {service.display_name} in {market.display_name}. "
                    f"Estimated uncached cost: ${plan.estimated_uncached_cost_usd}."
                ),
            },
        )
    if data_mode.value == "live" and plan.estimated_uncached_cost_usd > 0:
        return templates.TemplateResponse(
            request,
            "scan_result.html",
            {
                "dry_run": False,
                "message": (
                    "Live scan requires explicit cost confirmation in the dashboard API. "
                    f"Estimated uncached cost: ${plan.estimated_uncached_cost_usd}."
                ),
            },
        )
    try:
        result = await ScanPipeline(session, data_mode=data_mode).run(service, market, source="manual")
    except (DataForSEOError, RuntimeError) as exc:
        return templates.TemplateResponse(
            request,
            "scan_result.html",
            {"dry_run": False, "message": f"Scan failed: {exc}"},
        )
    created_message = (
        f"Created opportunity {result['opportunity_id']} with a preliminary assessment."
        if result["assessment_type"] == "preliminary"
        else f"Created opportunity {result['opportunity_id']} with score {result['score'].total_score}."
    )
    return templates.TemplateResponse(
        request,
        "scan_result.html",
        {
            "dry_run": False,
            "message": created_message,
        },
    )


@app.get("/opportunities/{opportunity_id}", response_class=HTMLResponse)
def opportunity_detail(
    request: Request, opportunity_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    opportunity = session.get(OpportunityORM, opportunity_id)
    artifacts = session.scalars(
        select(JsonArtifactORM)
        .where(JsonArtifactORM.opportunity_id == opportunity_id)
        .order_by(JsonArtifactORM.id.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "opportunity.html",
        {"opportunity": opportunity, "artifacts": artifacts},
    )
