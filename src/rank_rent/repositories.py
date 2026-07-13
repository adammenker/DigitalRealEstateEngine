from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import JsonArtifactORM, MarketORM, OpportunityORM, ServiceFamilyORM
from rank_rent.domain.models import Market, ServiceFamily


def upsert_service(session: Session, service: ServiceFamily) -> ServiceFamilyORM:
    slug = service.slug or service.id
    row = session.scalar(select(ServiceFamilyORM).where(ServiceFamilyORM.slug == slug))
    if row is None:
        row = ServiceFamilyORM(slug=slug, display_name=service.display_name)
        session.add(row)
    row.display_name = service.display_name
    row.description = service.description
    row.seed_queries = service.seed_queries
    row.negative_terms = service.negative_terms
    row.provider_categories = service.provider_categories
    row.regulated = service.regulated
    row.enabled = service.enabled
    session.flush()
    return row


def upsert_market(session: Session, market: Market) -> MarketORM:
    slug = market.slug or market.id
    row = session.scalar(select(MarketORM).where(MarketORM.slug == slug))
    if row is None:
        row = MarketORM(slug=slug, display_name=market.display_name)
        session.add(row)
    row.display_name = market.display_name
    row.type = market.type.value
    row.country_code = market.country_code
    row.state = market.state
    row.cities = market.cities
    row.postal_codes = market.postal_codes
    row.latitude = market.latitude
    row.longitude = market.longitude
    row.provider_location_code = market.provider_location_code
    row.provider_location_name = market.provider_location_name
    row.resolution_metadata = market.resolution_metadata
    session.flush()
    return row


def get_or_create_opportunity(
    session: Session, service: ServiceFamilyORM, market: MarketORM
) -> OpportunityORM:
    row = session.scalar(
        select(OpportunityORM).where(
            OpportunityORM.service_family_id == service.id,
            OpportunityORM.market_id == market.id,
        )
    )
    if row is None:
        row = OpportunityORM(service_family_id=service.id, market_id=market.id, status="discovered")
        session.add(row)
        session.flush()
    return row


def save_artifact(
    session: Session, opportunity_id: int | None, kind: str, payload: dict[str, object]
) -> JsonArtifactORM:
    row = JsonArtifactORM(opportunity_id=opportunity_id, kind=kind, payload=payload)
    session.add(row)
    session.flush()
    return row


def service_from_orm(row: ServiceFamilyORM) -> ServiceFamily:
    return ServiceFamily(
        id=row.slug,
        slug=row.slug,
        display_name=row.display_name,
        description=row.description,
        seed_queries=row.seed_queries,
        negative_terms=row.negative_terms,
        provider_categories=row.provider_categories,
        regulated=row.regulated,
        enabled=row.enabled,
    )


def market_from_orm(row: MarketORM) -> Market:
    return Market(
        id=row.slug,
        slug=row.slug,
        display_name=row.display_name,
        type=row.type,
        country_code=row.country_code,
        state=row.state,
        cities=row.cities,
        postal_codes=row.postal_codes,
        latitude=row.latitude,
        longitude=row.longitude,
        provider_location_code=row.provider_location_code,
        provider_location_name=row.provider_location_name,
        resolution_metadata=row.resolution_metadata,
    )
