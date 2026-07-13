from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from rank_rent.db.base import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ServiceFamilyORM(TimestampMixin, Base):
    __tablename__ = "service_families"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    seed_queries: Mapped[list[str]] = mapped_column(JSON, default=list)
    negative_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    regulated: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)


class MarketORM(TimestampMixin, Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(40), default="city")
    country_code: Mapped[str] = mapped_column(String(2), default="US")
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cities: Mapped[list[str]] = mapped_column(JSON, default=list)
    postal_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_location_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_location_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolution_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OpportunityORM(TimestampMixin, Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_family_id: Mapped[int] = mapped_column(ForeignKey("service_families.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    status: Mapped[str] = mapped_column(String(40), default="discovered")
    latest_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    missing_data_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    service_family: Mapped[ServiceFamilyORM] = relationship()
    market: Mapped[MarketORM] = relationship()


class ScanRunORM(TimestampMixin, Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RawApiResponseORM(TimestampMixin, Base):
    __tablename__ = "raw_api_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(120))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    api_version: Mapped[str] = mapped_column(String(40), default="fixture")
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    response_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    provider_task_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class JsonArtifactORM(TimestampMixin, Base):
    __tablename__ = "json_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderConfigORM(TimestampMixin, Base):
    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    provider_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    routing_notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=False)


class InterventionLogORM(TimestampMixin, Base):
    __tablename__ = "intervention_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    lifecycle_stage: Mapped[str] = mapped_column(String(80))
    action_type: Mapped[str] = mapped_column(String(80))
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text)
    recurs_for_every_property: Mapped[bool] = mapped_column(default=True)
    suggested_future_automation: Mapped[str] = mapped_column(Text, default="")

