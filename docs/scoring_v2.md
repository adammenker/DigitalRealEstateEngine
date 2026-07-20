# Scoring V2

Scoring V2 is configured in `config/scoring.yaml` and records a config hash on every score.

## Components

- `demand_evidence`: separate service-attractiveness and market-attractiveness sub-scores.
  National volume supports the service signal; measured or transparently estimated local
  volume supports the market signal. When only local volume exists, the service allocation
  remains unawarded so the same measurement cannot earn both sub-scores. Market thresholds
  are evidence-specific: measured local demand is scored against 50 monthly searches, while
  population-estimated demand is scored against 15 and capped at 40% of the market allocation.
  Service attractiveness receives 65% of demand points and market attractiveness receives
  35%, limiting the current population proxy to at most 3.36 of 24 points.
- `commercial_value`: CPC, paid competition, and transactional/commercial intent share. Their normalized internal shares are configured under `commercial.signal_shares`, converted into point budgets from the component weight, and independently capped so one signal cannot exceed its allocation.
- `competitor_weakness`: higher when visible competitors have weaker link authority or lower service and local relevance. Direct relevance combines service relevance (55%), local relevance (25%), and their interaction (20%), so a locally relevant service page is more threatening than a page supported by only one signal. Authority enrichment remains deduplicated by domain, but scoring weights every distinct query-position observation by representative keyword value and organic position. A domain ranking repeatedly across valuable queries therefore contributes more exposure than a domain seen once. Directory aggregators, marketplaces, lead generators, national service brands, local providers, and publishers receive separate configurable adjustments. National service brands lower weakness because they represent a stronger direct threat; they do not receive an aggregator bonus.
- `organic_click_availability`: higher when SERPs have fewer directories, marketplaces, national brands, lead generators, informational publishers, shopping/product features, ads, and local-pack displacement. Unknown results receive a smaller configurable uncertainty penalty rather than being treated as harmless. Organic results are weighted by positions 1-10 against a fixed top-10 capacity, while shopping features, ads, and local packs are penalized by their share of the sampled SERPs. Each SERP is additionally weighted by the relative demand and CPC of its representative keyword when matching metrics are available.
- `provider_suitability`: higher when there are enough providers with independently supported service fit, geographic fit, operating-status certainty, contactability, and reputation, without severe oversupply. Preferred-range and oversupply adjustments use suitable providers rather than the raw listing count. Tenant quality uses the average of the five highest-scoring suitable providers, so irrelevant or inactive listing noise cannot depress an otherwise credible tenant pool.
- `data_completeness`: separates evidence quality from opportunity attractiveness.

Missing evidence reduces confidence and applies field-specific attractiveness penalties configured under `missing_data_penalties`. These penalties distinguish unknown evidence from observed poor performance and are capped in aggregate by `missing_data_penalty_max`. Confidence independently records deductions and caps for missing local demand, estimation use, source mode, data age, representative SERP count, competitor count, provider count, and SERP classification coverage. Unknown results occupying more than 25% of position-and-keyword-weighted organic capacity cap confidence at `medium`. Fixture evidence is capped `low`; sandbox, preliminary, stale, undersampled, population-estimated, and poorly classified evidence is capped `medium` where applicable.

Full assessments are labeled `complete`, `partial`, or `unusable`. Missing competitor evidence makes a full assessment unusable and applies the configured critical score cap. Missing SERP evidence makes it partial and applies its configured cap. Preliminary scans retain the separate `preliminary` status. Only complete full assessments replace an opportunity's ranked score.

Default missing-evidence attractiveness severities are 1 point for CPC, 3 for local demand,
4 for providers, 8 for competitors, and 10 for SERPs. Missing all keyword metrics carries
8 points, and a SERP snapshot with no result evidence carries the same 10-point severity as
a missing snapshot. When several fields are absent, penalties retain these proportions while
scaling down to the configured aggregate maximum.

Confidence details are persisted under `input_measurements.confidence_model` and copied into each discovery report. Preliminary testing scans should not be treated as final market scores.

## Calculation Traces

Every component stores a component-specific underwriting trace containing:

- the earned score and maximum score;
- the exact formula and thresholds used;
- only the measurements consumed by that component;
- signed calculation steps that reconcile to the component score;
- a fact-specific explanation derived from the observed evidence.

Competitor traces include every observed query/position appearance, keyword-value weight,
position weight, combined exposure weight, per-domain link weakness, separate service and
local relevance, their interaction, combined direct relevance, archetype adjustment, weighted
threat, and component contribution. Repeated domains reuse one authority record while all
distinct SERP observations contribute to exposure. Historical records without observation
provenance use their representative position or the explicit `unpositioned_weight`
compatibility setting. Missing-data penalties remain separate from component scores so their
effect on the total is explicit.

Organic-click traces include each result's position weight, each representative keyword's
demand/CPC weight, weighted classification shares, and the weighted SERP share affected by
shopping features, local packs, and top ads. Unmatched representative keywords use the
explicit compatibility weight configured in `organic_click.keyword_weighting`.
