from __future__ import annotations

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
from rank_rent.services.scanner import ScanPipeline

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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/opportunities")
def api_opportunities(session: Session = Depends(get_session)) -> dict[str, Any]:
    opportunities = session.scalars(select(OpportunityORM).order_by(OpportunityORM.id.desc())).all()
    return {"opportunities": [_opportunity_summary(row) for row in opportunities]}


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
            "message": (
                f"Interpreted as {service.display_name} in {market.display_name}. "
                "Estimated fixture cost: $0.00."
            ),
            "resolved": {"service": service.display_name, "market": market.display_name},
        }
    result = await ScanPipeline(session).run(service, market, source="manual")
    return {
        "dry_run": False,
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
def dashboard(request: Request, session: Session = Depends(get_session)):
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
):
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
):
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
