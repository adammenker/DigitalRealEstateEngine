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
version: v2.6
weights:
  demand_evidence: 24
  commercial_value: 16
  competitor_weakness: 22
  organic_click_availability: 16
  provider_suitability: 14
  data_completeness: 8
missing_data_penalty_max: 24
missing_data_penalties:
  keyword_metrics: 8
  keyword_cpc: 1
  local_demand: 3
  provider_candidates: 4
  competitor_metrics: 8
  serp_snapshots: 10
  serp_results: 10
missing_evidence_score_caps:
  competitor_metrics: 40
  serp_snapshots: 60
  serp_results: 60
data_completeness_expected_groups: 7
demand:
  market_estimator: population_share
  strong_national_monthly_volume: 900
  strong_market_monthly_volume: 900
  service_attractiveness_share: 0.35
  market_attractiveness_share: 0.65
confidence:
  thresholds:
    high: 85
    medium: 65
    low: 40
  keyword_max_age_days: 90
  serp_max_age_days: 30
  representative_serp_target: 3
  competitor_sample_target: 3
  provider_sample_target: 2
  source_mode_penalties:
    live: 0
    replay: 5
    sandbox: 15
    fixture: 25
    unknown: 10
  deductions:
    missing_field: 8
    missing_local_demand: 20
    low_confidence_market_estimate: 12
    medium_confidence_market_evidence: 4
    stale_keyword_metrics: 10
    stale_serps: 15
    limited_serp_sample: 10
    limited_competitor_sample: 10
    limited_provider_sample: 8
commercial:
  strong_cpc: 28
  strong_paid_competition: 0.8
  signal_shares:
    cpc: 0.5625
    paid_competition: 0.1875
    high_intent: 0.25
competitors:
  weak_referring_domains: 35
  strong_referring_domains: 260
  unknown_referring_domains_weakness: 0.45
  relevance_threat_strength: 0.65
  unpositioned_weight: 1.0
  serp_position_weights:
    1: 1.00
    2: 0.90
    3: 0.80
    4: 0.65
    5: 0.55
    6: 0.45
    7: 0.35
    8: 0.28
    9: 0.22
    10: 0.18
  archetype_weakness_adjustments:
    directory: 0.15
    marketplace: 0.08
    lead_generator: 0.04
    national_brand: -0.1
    local_provider: 0
    informational_publisher: 0.08
    government_or_nonprofit: 0
    unknown: 0
organic_click:
  serp_position_weights:
    1: 1.00
    2: 0.90
    3: 0.80
    4: 0.65
    5: 0.55
    6: 0.45
    7: 0.35
    8: 0.28
    9: 0.22
    10: 0.18
  keyword_weighting:
    demand_share: 0.60
    commercial_share: 0.40
    minimum_weight: 0.25
    unmatched_keyword_weight: 1.00
  directory_penalty: 0.35
  marketplace_penalty: 0.3
  national_brand_penalty: 0.3
  lead_generator_penalty: 0.2
  informational_publisher_penalty: 0.1
  shopping_product_penalty: 0.16
  shopping_product_result_types:
    - shopping
    - popular_products
    - product_considerations
    - refine_products
    - explore_brands
  local_pack_penalty: 0.14
  ads_top_penalty: 0.12
providers:
  suitable_threshold: 55
  inactive_score_cap: 40
  signal_weights:
    service_fit: 30
    geographic_fit: 25
    status_certainty: 15
    contactability: 20
    reputation: 10
  status_scores:
    open: 1
    closed_now: 1
    active: 1
    operational: 1
    unknown: 0.25
    temporarily_closed: 0
    closed: 0
    closed_forever: 0
    permanently_closed: 0
  geography:
    full_credit_distance_km: 25
    max_distance_km: 100
    service_area_match_score: 1
    address_match_score: 0.85
  contactability:
    channel_strengths:
      website: 0.3
      phone: 0.85
      email: 1
      contact_form: 0.9
    unknown_confidence: 0.5
    confidence_floor: 0.75
  reputation:
    rating_share: 0.7
    review_count_share: 0.3
    review_saturation_count: 100
  ideal_min: 2
  ideal_max: 8
  oversupply_count: 14
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
    assert result["score"].input_measurements["confidence_model"]["source_mode"] == "fixture"
    assert result["site_path"] is None
    assert result["assessment_type"] == "full"
