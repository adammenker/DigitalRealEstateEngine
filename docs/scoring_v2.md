# Scoring V2

Scoring V2 is configured in `config/scoring.yaml` and records a config hash on every score.

## Components

- `demand_evidence`: separate service-attractiveness and market-attractiveness sub-scores. National volume supports the service signal; measured or transparently estimated local volume supports the market signal.
- `commercial_value`: CPC, paid competition, and transactional/commercial intent share.
- `competitor_weakness`: higher when visible competitors have weaker link authority or lower service and local relevance. Directory aggregators, marketplaces, lead generators, national service brands, local providers, and publishers receive separate configurable adjustments. National service brands lower weakness because they represent a stronger direct threat; they do not receive an aggregator bonus.
- `organic_click_availability`: higher when SERPs have fewer directories, marketplaces, national brands, lead generators, informational publishers, shopping/product features, ads, and local-pack displacement. Each result-class penalty and the recognized shopping/product result types are configurable in `config/scoring.yaml`.
- `provider_suitability`: higher when there are enough providers with independently supported service fit, geographic fit, operating-status certainty, contactability, and reputation, without severe oversupply.
- `data_completeness`: separates evidence quality from opportunity attractiveness.

Missing evidence reduces confidence and applies explicit penalties. Confidence starts from a configured evidence-quality score and records deductions and caps for missing local demand, estimation use, source mode, data age, representative SERP count, competitor count, and provider count. Fixture evidence is capped `low`; sandbox, preliminary, stale, undersampled, and population-estimated evidence is capped `medium` where applicable.

Confidence details are persisted under `input_measurements.confidence_model` and copied into each discovery report. Preliminary testing scans should not be treated as final market scores.

## Calculation Traces

Every component stores a component-specific underwriting trace containing:

- the earned score and maximum score;
- the exact formula and thresholds used;
- only the measurements consumed by that component;
- signed calculation steps that reconcile to the component score;
- a fact-specific explanation derived from the observed evidence.

Competitor traces include per-domain link weakness, direct relevance, archetype adjustment,
and normalized component contribution. Missing-data penalties remain separate from component
scores so their effect on the total is explicit.
