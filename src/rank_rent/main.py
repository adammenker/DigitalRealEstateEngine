from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.base import get_session, init_db
from rank_rent.db.orm import (
    ApiCallORM,
    CompetitorMetricORM,
    JsonArtifactORM,
    KeywordClusterORM,
    KeywordDecisionORM,
    KeywordMetricORM,
    OpportunityORM,
    ProviderCandidateORM,
    ScanPlanCallORM,
    ScanRunORM,
    SerpSnapshotORM,
)
from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    Market,
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
from rank_rent.services.discovery_report import build_discovery_report
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
from rank_rent.services.records import save_scan_plan_calls
from rank_rent.services.scan_worker import active_retry_for_scan, scan_worker_loop
from rank_rent.services.scanner import ScanPipeline
from rank_rent.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    settings = get_settings()
    stop_event: asyncio.Event | None = None
    worker_task: asyncio.Task[None] | None = None
    if settings.scan_worker_enabled:
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(
            scan_worker_loop(
                stop_event,
                poll_seconds=settings.scan_worker_poll_seconds,
                heartbeat_seconds=settings.scan_worker_heartbeat_seconds,
                stale_after_seconds=settings.scan_worker_stale_after_seconds,
            )
        )
    try:
        yield
    finally:
        if stop_event is not None:
            stop_event.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(
                    worker_task,
                    timeout=max(1.0, settings.scan_worker_heartbeat_seconds + 1.0),
                )
            except TimeoutError:
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task


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
    service_text: str
    location_text: str
    country: str = "US"
    selected_location: LocationCandidate | None = None
    dry_run: bool = False
    async_run: bool = False
    confirm_live_cost: bool = False
    data_mode: str | None = None


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
        "cancel_requested": row.cancel_requested,
        "worker_id": row.worker_id,
        "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
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


def _latest_score_payload(session: Session, opportunity_id: int) -> dict[str, Any] | None:
    payload = _latest_scan_payload(session, opportunity_id)
    if payload and isinstance(payload.get("score"), dict):
        return cast(dict[str, Any], payload["score"])
    rescore = _latest_artifact_payload(session, opportunity_id, "rescore_result")
    if rescore and isinstance(rescore.get("score"), dict):
        return cast(dict[str, Any], rescore["score"])
    return None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    settings = get_settings()
    data_mode = validate_runtime_mode(settings)
    return {
        "data_mode": data_mode.value,
        "synthetic_fixture_data": data_mode.value == "fixture",
        "live_api_calls_allowed": settings.allow_live_api_calls,
        "live_scan_depth": settings.live_scan_depth,
        "dataforseo_environment": settings.dataforseo_environment,
        "dataforseo_sandbox": settings.dataforseo_environment.strip().lower() == "sandbox",
        "requires_live_cost_confirmation": data_mode.value == "live",
        "geocoder": {
            "pelias_enabled": bool(settings.pelias_base_url.strip()),
            "pelias_base_url_configured": bool(settings.pelias_base_url.strip()),
            "fallback_sources": ["explicit", "seed", "database", "dataforseo-cache"],
        },
    }


@app.get("/api/locations/search")
async def api_location_search(
    q: str,
    country: str = "US",
    limit: int = 8,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    settings = get_settings()
    candidates = await search_locations(
        session=session,
        query=q,
        country=country,
        settings=settings,
        limit=max(1, min(limit, 12)),
    )
    return {"locations": [candidate.model_dump(mode="json") for candidate in candidates]}


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
        "opportunities": [_opportunity_summary(row) for row in opportunities],
    }


@app.get("/api/opportunities/compare")
def api_opportunity_compare(ids: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    opportunity_ids = [int(item.strip()) for item in ids.split(",") if item.strip().isdigit()]
    if not opportunity_ids:
        raise HTTPException(status_code=400, detail="Provide one or more numeric opportunity ids.")
    rows = session.scalars(
        select(OpportunityORM).where(OpportunityORM.id.in_(opportunity_ids)).order_by(OpportunityORM.id)
    ).all()
    return {
        "opportunities": [
            {
                "opportunity": _opportunity_summary(row),
                "latest_report": _latest_artifact_payload(session, row.id, "discovery_report"),
                "latest_score": _latest_score_payload(session, row.id),
            }
            for row in rows
        ]
    }


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
    return {
        "data_mode": _artifact_data_mode(artifacts),
        "opportunity": _opportunity_summary(opportunity),
        "keyword_decisions": _keyword_decision_rows(session, latest_scan.id) if latest_scan else [],
        "keyword_clusters": _keyword_cluster_rows(session, latest_scan.id) if latest_scan else [],
        "latest_scan": _scan_summary(latest_scan, session) if latest_scan else None,
        "api_calls": _api_call_rows(session, latest_scan.id) if latest_scan else [],
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
    opportunity_id: int, session: Session = Depends(get_session)
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
    market_payload = request.get("market_payload")
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
    score = OpportunityScorer().score(metrics, serp_snapshots, competitors, providers, market)
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
            "scan_profile": latest_scan.scan_profile,
            "planned_cost_usd": latest_scan.planned_cost_usd,
            "actual_cost_usd": latest_scan.actual_cost_usd,
        },
    )
    opportunity.latest_score = score.total_score
    opportunity.score_version = score.scoring_version
    opportunity.confidence = score.confidence.value
    opportunity.missing_data_flags = score.missing_fields
    session.add(
        JsonArtifactORM(
            opportunity_id=opportunity_id,
            kind="rescore_result",
            payload={
                "score": score.model_dump(mode="json"),
                "discovery_report": report,
                "source_scan_run_id": latest_scan.id,
            },
        )
    )
    session.commit()
    return {
        "rescored": True,
        "opportunity": _opportunity_summary(opportunity),
        "score": score.model_dump(mode="json"),
        "discovery_report": report,
    }


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
    service = ServiceFamily(**service_payload)
    market = Market(**market_payload)
    plan = build_scan_plan(get_settings(), resolve_data_mode(data_mode), service, market, session=session)
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
    service = ServiceFamily(
        id=payload.service_text,
        display_name=payload.service_text.title(),
        seed_queries=[payload.service_text],
        negative_terms=["diy", "jobs", "salary"],
        intent_modifiers=DEFAULT_AD_HOC_INTENT_MODIFIERS,
        negative_product_terms=DEFAULT_NEGATIVE_PRODUCT_TERMS,
    )
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
    if payload.dry_run:
        plan = build_scan_plan(settings, requested_mode, service, market, session=session)
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
                "market": market.model_dump(mode="json"),
            },
        }
    plan = build_scan_plan(settings, requested_mode, service, market, session=session)
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
        scan = _queue_scan(session, service, market, requested_mode.value, plan.model_dump(mode="json"))
        return {
            "dry_run": False,
            "queued": True,
            "data_mode": requested_mode.value,
            "synthetic_fixture_data": requested_mode.value == "fixture",
            "assessment_type": "pending",
            "scan_plan": plan.model_dump(mode="json"),
            "message": f"Queued scan {scan.id}.",
            "scan_id": scan.id,
            "opportunity_id": scan.opportunity_id,
        }
    try:
        result = await ScanPipeline(session, data_mode=requested_mode).run(
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
) -> ScanRunORM:
    service_row = upsert_service(session, service)
    market_row = upsert_market(session, market)
    opportunity = get_or_create_opportunity(session, service_row, market_row)
    scan = ScanRunORM(
        opportunity_id=opportunity.id,
        source="manual_async",
        status="queued",
        estimated_cost_usd=float(scan_plan.get("estimated_uncached_cost_usd") or 0),
        planned_cost_usd=float(scan_plan.get("estimated_uncached_cost_usd") or 0),
        data_mode=data_mode,
        scan_profile=str(scan_plan.get("scan_profile") or "testing"),
        source_scan_run_id=source_scan_run_id,
        retry_count=retry_count,
        progress_stage="queued",
        cache_policy_version="v2",
        integration_versions={
            "data_mode": data_mode,
            "queued": True,
            "async_worker": "database_in_process",
        },
        request_parameters={
            "service": service.slug,
            "market": market.slug,
            "data_mode": data_mode,
            "scan_plan": scan_plan,
            "service_payload": service.model_dump(mode="json"),
            "market_payload": market.model_dump(mode="json"),
        },
    )
    session.add(scan)
    session.flush()
    plan = build_scan_plan(get_settings(), resolve_data_mode(data_mode), service, market, session=session)
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
    service = ServiceFamily(
        id=service_text,
        display_name=service_text.title(),
        seed_queries=[service_text],
        negative_terms=["diy", "jobs", "salary"],
        intent_modifiers=DEFAULT_AD_HOC_INTENT_MODIFIERS,
        negative_product_terms=DEFAULT_NEGATIVE_PRODUCT_TERMS,
    )
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
