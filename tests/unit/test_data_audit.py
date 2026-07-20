from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import MarketORM, OpportunityORM, ScanRunORM, ServiceFamilyORM
from rank_rent.services.data_audit import audit_data


def test_data_audit_reports_current_local_counts() -> None:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        service = ServiceFamilyORM(slug="drywall", display_name="Drywall")
        market = MarketORM(slug="st-louis-mo", display_name="St. Louis, MO")
        session.add_all([service, market])
        session.flush()
        opportunity = OpportunityORM(
            service_family_id=service.id,
            market_id=market.id,
            status="review_required",
        )
        session.add(opportunity)
        session.flush()
        scan = ScanRunORM(
            opportunity_id=opportunity.id,
            source="manual",
            status="completed",
            estimated_cost_usd=2.5,
            actual_cost_usd=0,
        )
        session.add(scan)
        session.commit()

        audit = audit_data(session)
        assert audit["scan_count"] == 1
        assert audit["scan_statuses"] == {"completed": 1}
        assert audit["opportunity_count"] == 1
        assert audit["raw_response_count"] == 0
        assert audit["typed_record_counts"] == {
            "market_prefilter_runs": 0,
            "market_prefilter_assessments": 0,
            "keyword_metrics": 0,
            "serp_snapshots": 0,
            "competitor_metrics": 0,
            "provider_candidates": 0,
        }
