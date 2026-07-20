# Scoring V2

Scoring V2 is configured in `config/scoring.yaml` and records a config hash on every score.

## Components

- `demand_evidence`: separate service-attractiveness and market-attractiveness sub-scores.
  National volume supports the service signal; measured or transparently estimated local
  volume supports the market signal. When only local volume exists, the service allocation
  remains unawarded so the same measurement cannot earn both sub-scores.
- `commercial_value`: CPC, paid competition, and transactional/commercial intent share. Their normalized internal shares are configured under `commercial.signal_shares`, converted into point budgets from the component weight, and independently capped so one signal cannot exceed its allocation.
- `competitor_weakness`: higher when visible competitors have weaker link authority or lower service and local relevance. Each unique domain is weighted by its best observed organic position, so competitors occupying the first few results influence ranking difficulty more than results near the bottom of page one. Directory aggregators, marketplaces, lead generators, national service brands, local providers, and publishers receive separate configurable adjustments. National service brands lower weakness because they represent a stronger direct threat; they do not receive an aggregator bonus.
- `organic_click_availability`: higher when SERPs have fewer directories, marketplaces, national brands, lead generators, informational publishers, shopping/product features, ads, and local-pack displacement. Classified organic results are weighted by positions 1-10 against a fixed top-10 capacity, while shopping features, ads, and local packs are penalized by their share of the sampled SERPs. Each SERP is additionally weighted by the relative demand and CPC of its representative keyword when matching metrics are available.
- `provider_suitability`: higher when there are enough providers with independently supported service fit, geographic fit, operating-status certainty, contactability, and reputation, without severe oversupply. Preferred-range and oversupply adjustments use suitable providers rather than the raw listing count.
- `data_completeness`: separates evidence quality from opportunity attractiveness.

Missing evidence reduces confidence and applies field-specific attractiveness penalties configured under `missing_data_penalties`. These penalties distinguish unknown evidence from observed poor performance and are capped in aggregate by `missing_data_penalty_max`. Confidence independently records deductions and caps for missing local demand, estimation use, source mode, data age, representative SERP count, competitor count, and provider count. Fixture evidence is capped `low`; sandbox, preliminary, stale, undersampled, and population-estimated evidence is capped `medium` where applicable.

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

Competitor traces include the representative query and organic position, every observed
query/position appearance, configured position weight, per-domain link weakness, direct
relevance, archetype adjustment, and weighted component contribution. Repeated domains are
scored once using their best observed position. Historical records without position provenance
use the explicit `unpositioned_weight` compatibility setting. Missing-data penalties remain
separate from component scores so their effect on the total is explicit.

Organic-click traces include each result's position weight, each representative keyword's
demand/CPC weight, weighted classification shares, and the weighted SERP share affected by
shopping features, local packs, and top ads. Unmatched representative keywords use the
explicit compatibility weight configured in `organic_click.keyword_weighting`.
