import asyncio

from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import JsonArtifactORM  # noqa: F401
from rank_rent.runtime import DataMode
from rank_rent.services.scanner import ScanPipeline
from rank_rent.services.seeds import load_markets, load_services


def test_fixture_pipeline_records_scan_without_site_side_effects(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config/outreach_templates").mkdir(parents=True)
    (tmp_path / "config/scoring.yaml").write_text(
        """
version: v1
weights:
  demand: 25
  commercial_intent: 15
  organic_accessibility: 30
  serp_accessibility: 15
  provider_supply: 15
missing_data_penalty_max: 30
thresholds:
  high_confidence_missing_fields: 1
  medium_confidence_missing_fields: 4
"""
    )
    (tmp_path / "config/outreach_templates/initial_email.txt").write_text(
        "Subject: Pilot lead opportunity for {service} in {market}\n\n"
        "Hi {provider_name},\n\n"
        "I am building a local referral property for people looking for {service} around {market}. "
        "No existing volume is being promised.\n\nThanks,\n{sender_name}\n"
    )
    service = load_services(
        __import__("pathlib").Path(__file__).parents[2] / "seeds/services.example.yaml"
    )[0]
    market = load_markets(
        __import__("pathlib").Path(__file__).parents[2] / "seeds/locations.example.yaml"
    )[0]
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        result = asyncio.run(
            ScanPipeline(session, data_mode=DataMode.fixture).run(service, market, source="fixture")
        )
    assert result["score"].total_score > 0
    assert result["site_path"] is None
    assert result["assessment_type"] == "full"
