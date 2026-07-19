# Scoring V2

Scoring V2 is configured in `config/scoring.yaml` and records a config hash on every score.

## Components

- `demand_evidence`: included keyword volume and high-intent demand strength.
- `commercial_value`: CPC, paid competition, and transactional/commercial intent share.
- `competitor_weakness`: higher when visible competitors are weaker or are aggregators rather than strong local providers.
- `organic_click_availability`: higher when SERPs have fewer directories, national brands, lead generators, ads, and local-pack displacement.
- `provider_suitability`: higher when there are enough contactable local providers without severe oversupply.
- `data_completeness`: separates evidence quality from opportunity attractiveness.

Missing evidence reduces confidence and applies explicit penalties. Preliminary testing scans should not be treated as final market scores.
