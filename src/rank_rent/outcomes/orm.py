from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from rank_rent.db.base import Base
from rank_rent.db.orm import TimestampMixin, now_utc


class PropertyDecisionORM(TimestampMixin, Base):
    __tablename__ = "property_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["full_score_id", "scan_run_id"],
            ["full_opportunity_scores.id", "full_opportunity_scores.scan_run_id"],
            name="fk_property_decision_score_scan",
        ),
        ForeignKeyConstraint(
            ["evidence_snapshot_id", "scan_run_id"],
            ["json_artifacts.id", "json_artifacts.scan_run_id"],
            name="fk_property_decision_evidence_scan",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    full_score_id: Mapped[int] = mapped_column(Integer)
    evidence_snapshot_id: Mapped[int] = mapped_column(Integer)
    score_version_at_selection: Mapped[str] = mapped_column(String(80))
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_family_slug: Mapped[str] = mapped_column(String(120), index=True)
    market_size_band: Mapped[str] = mapped_column(String(80), index=True)
    evidence_quality: Mapped[str] = mapped_column(String(40), index=True)
    validated_opportunity_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    selection_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


@event.listens_for(PropertyDecisionORM, "before_update")
@event.listens_for(PropertyDecisionORM, "before_delete")
def _prevent_property_decision_mutation(
    _mapper: object,
    _connection: object,
    _target: PropertyDecisionORM,
) -> None:
    raise ValueError("property_decision_is_immutable")


class PropertyOutcomeORM(TimestampMixin, Base):
    __tablename__ = "property_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "property_decision_id",
            "source_name",
            "source_record_id",
            name="uq_property_outcome_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_decision_id: Mapped[int] = mapped_column(
        ForeignKey("property_decisions.id"), index=True
    )
    period_date: Mapped[date] = mapped_column(Date, index=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(120))
    source_record_id: Mapped[str] = mapped_column(String(160))
    truth_basis: Mapped[str] = mapped_column(String(40), index=True)
    confidence: Mapped[str] = mapped_column(String(40))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    average_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    organic_sessions: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    forms: Mapped[int] = mapped_column(Integer, default=0)
    qualified_leads: Mapped[int] = mapped_column(Integer, default=0)
    appointments: Mapped[int] = mapped_column(Integer, default=0)
    won_jobs: Mapped[int] = mapped_column(Integer, default=0)
    reported_revenue: Mapped[float] = mapped_column(Float, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_suitability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    addressable_market_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CalibrationReportORM(TimestampMixin, Base):
    __tablename__ = "calibration_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_version: Mapped[str] = mapped_column(String(80))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    property_count: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScoringChangeReviewORM(TimestampMixin, Base):
    __tablename__ = "scoring_change_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(120), unique=True)
    current_version: Mapped[str] = mapped_column(String(80))
    proposed_version: Mapped[str] = mapped_column(String(80))
    initiated_by: Mapped[str] = mapped_column(String(120))
    reviewer_id: Mapped[str] = mapped_column(String(120))
    benchmark_run_id: Mapped[str] = mapped_column(String(120))
    benchmark_passed: Mapped[bool] = mapped_column(Boolean)
    rationale: Mapped[str] = mapped_column(Text)
    authorized_for_manual_application: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_automatically: Mapped[bool] = mapped_column(Boolean, default=False)
