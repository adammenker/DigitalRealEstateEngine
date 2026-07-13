from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.base import get_session, init_db
from rank_rent.db.orm import JsonArtifactORM, OpportunityORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOError
from rank_rent.runtime import resolve_data_mode, validate_runtime_mode
from rank_rent.services.scanner import ScanPipeline
from rank_rent.settings import get_settings

init_db()
app = FastAPI(title="Digital Real Estate Engine")
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
    dry_run: bool = False
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


def _artifact_data_mode(artifacts: Sequence[JsonArtifactORM]) -> str:
    for artifact in artifacts:
        if artifact.kind == "scan_result":
            mode = artifact.payload.get("data_mode")
            if isinstance(mode, str):
                return mode
    return validate_runtime_mode(get_settings()).value


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
        "opportunities": [_opportunity_summary(row) for row in opportunities],
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
    return {
        "data_mode": _artifact_data_mode(artifacts),
        "opportunity": _opportunity_summary(opportunity),
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


@app.post("/api/scans")
async def api_scan(payload: ScanRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    settings = get_settings()
    data_mode = validate_runtime_mode(settings)
    requested_mode = resolve_data_mode(payload.data_mode or data_mode)
    validate_runtime_mode(settings, requested_mode)
    service = ServiceFamily(
        id=payload.service_text,
        display_name=payload.service_text.title(),
        seed_queries=[payload.service_text],
        negative_terms=["diy", "jobs", "salary"],
    )
    market = Market(
        id=payload.location_text,
        display_name=payload.location_text,
        country_code=payload.country,
    )
    if payload.dry_run:
        return {
            "dry_run": True,
            "data_mode": requested_mode.value,
            "synthetic_fixture_data": requested_mode.value == "fixture",
            "message": (
                f"Interpreted as {service.display_name} in {market.display_name}. "
                "Estimated fixture cost: $0.00."
            ),
            "resolved": {"service": service.display_name, "market": market.display_name},
        }
    try:
        result = await ScanPipeline(session, data_mode=requested_mode).run(
            service,
            market,
            source="manual",
        )
    except DataForSEOError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "dry_run": False,
        "data_mode": result["data_mode"],
        "synthetic_fixture_data": result["data_mode"] == "fixture",
        "message": (
            f"Created opportunity {result['opportunity_id']} "
            f"with score {result['score'].total_score}."
        ),
        "opportunity_id": result["opportunity_id"],
        "scan_id": result["scan_id"],
        "score": result["score"].model_dump(mode="json"),
        "site_path": str(result["site_path"]) if result["site_path"] else None,
    }


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
    service = ServiceFamily(
        id=service_text,
        display_name=service_text.title(),
        seed_queries=[service_text],
        negative_terms=["diy", "jobs", "salary"],
    )
    market = Market(id=location_text, display_name=location_text, country_code=country)
    if dry_run:
        return templates.TemplateResponse(
            request,
            "scan_result.html",
            {
                "dry_run": True,
                "message": f"Interpreted as {service.display_name} in {market.display_name}. Estimated fixture cost: $0.00.",
            },
        )
    result = await ScanPipeline(session).run(service, market, source="manual")
    return templates.TemplateResponse(
        request,
        "scan_result.html",
        {
            "dry_run": False,
            "message": f"Created opportunity {result['opportunity_id']} with score {result['score'].total_score}.",
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
