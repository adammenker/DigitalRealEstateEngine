from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import (
    CompetitorMetricORM,
    JsonArtifactORM,
    KeywordMetricORM,
    MarketPrefilterAssessmentORM,
    MarketPrefilterRunORM,
    OpportunityORM,
    ProviderCandidateORM,
    RawApiResponseORM,
    ScanRunORM,
    SerpSnapshotORM,
)


def audit_data(session: Session) -> dict[str, Any]:
    scans = session.scalars(select(ScanRunORM)).all()
    opportunities = session.scalars(select(OpportunityORM)).all()
    artifacts = session.scalars(select(JsonArtifactORM)).all()
    raw_responses = session.scalars(select(RawApiResponseORM)).all()
    return {
        "scan_count": len(scans),
        "scan_statuses": dict(Counter(scan.status for scan in scans)),
        "opportunity_count": len(opportunities),
        "opportunity_statuses": dict(Counter(row.status for row in opportunities)),
        "artifact_kinds": dict(Counter(artifact.kind for artifact in artifacts)),
        "raw_response_count": len(raw_responses),
        "raw_response_cost_usd": round(sum(row.cost_usd or 0 for row in raw_responses), 6),
        "typed_record_counts": {
            "market_prefilter_runs": _count(session, MarketPrefilterRunORM),
            "market_prefilter_assessments": _count(
                session,
                MarketPrefilterAssessmentORM,
            ),
            "keyword_metrics": _count(session, KeywordMetricORM),
            "serp_snapshots": _count(session, SerpSnapshotORM),
            "competitor_metrics": _count(session, CompetitorMetricORM),
            "provider_candidates": _count(session, ProviderCandidateORM),
        },
    }


def _count(session: Session, model: type[Any]) -> int:
    return len(session.scalars(select(model.id)).all())
