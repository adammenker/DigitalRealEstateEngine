# DigitalRealEstateEngine — Discovery Completion Specification

**Target repository:** `https://github.com/adammenker/DigitalRealEstateEngine`  
**Baseline commit:** `b92d6c0043dbf0b8ae4563122154fed9fad28c66`  
**Audience:** Codex / coding agent  
**Purpose:** Complete and harden the opportunity-discovery engine before beginning domain generation, outreach workflows, site generation, deployment, or real-world validation.

---

## 1. Objective

The discovery engine must reliably transform a service family and local market into an explainable opportunity assessment:

```text
service family + local market
→ resolve geography
→ generate and cluster high-intent keywords
→ retrieve or replay market evidence
→ analyze search-result competition
→ estimate organic click availability
→ evaluate provider suitability
→ calculate a versioned score
→ rank opportunities
→ show a complete underwriting report
```

The system should help the user decide which opportunity deserves further manual review.

The engine must not claim that:

- Google rankings are guaranteed.
- Search volume is more geographically precise than the source data supports.
- A high score proves that the business will be profitable.
- A provider will accept the leads or pay rent.
- Preliminary scans are equivalent to full scans.

---

## 2. Scope

This specification includes only discovery-related work:

1. Scan-execution correctness
2. Request planning and cost accounting
3. Scoring V2
4. Demand modeling
5. Keyword analysis
6. SERP classification
7. Competitor analysis
8. Provider suitability
9. Discovery UI and reports
10. Offline tests and replay validation
11. Discovery exit criteria

This specification deliberately excludes:

- Domain-name generation
- Domain-availability APIs
- Provider outreach generation
- Email sending
- Site configuration
- Static site generation
- Cloud deployment
- Forms and lead routing
- Twilio
- GA4 and Search Console
- Provider billing
- Real-world validation scans across selected markets

Real-world validation should happen after additional development iterations and is not part of this implementation.

---

## 3. Codex execution rules

1. Confirm the repository is based on commit `b92d6c0`.
2. Inspect the actual repository before editing.
3. Implement milestones in the order listed.
4. Preserve fixture, replay, and live modes.
5. Use fixture and replay data by default.
6. Do not make production DataForSEO calls.
7. Sandbox calls must be explicit and cost bounded.
8. Do not add downstream property-generation features.
9. Add tests for every scoring and lifecycle change.
10. Do not weaken existing tests.
11. Keep the repository runnable after each milestone.
12. Update:
    - `docs/current_state.md`
    - `docs/hardening_baseline.md`
    - `docs/hardening_deviations.md`
13. Record any necessary deviation in `docs/discovery_deviations.md`.
14. Prefer simple schema resets over extensive migration compatibility because the project remains pre-production.
15. Preserve purchased raw API responses and replay bundles outside disposable database state.
16. Run `make verify` after each milestone.

---

## 4. Discovery completion definition

Discovery is complete only when the system can answer all of these questions for an opportunity:

- What service and local market were analyzed?
- How was the market resolved?
- Which keyword candidates were generated?
- Which keywords were excluded, grouped, or selected?
- What demand evidence was returned?
- What geographic granularity does that demand represent?
- Which search-result pages were inspected?
- Why were those queries selected?
- What kinds of results dominate the SERP?
- How strong and locally relevant are the current ranking competitors?
- How much ordinary organic click opportunity remains?
- Are there several plausible local providers?
- Which evidence is missing, stale, synthetic, replayed, or live?
- What did the scan cost?
- Why did the opportunity receive each component score?
- Why does it rank above or below another opportunity?
- Can the result be replayed and rescored without new paid calls?

---

# Milestone 1 — Scan execution correctness

## 5. Unify planning and execution

The current planner and executor must use one exact request graph.

Create a shared immutable execution plan:

```python
class ScanExecutionPlan(BaseModel):
    scan_profile: ScanProfile
    service_family_id: str
    market_id: str
    keyword_seed_requests: list[KeywordSeedRequest]
    keyword_metrics_requests: list[KeywordMetricsRequest]
    serp_requests: list[SerpRequest]
    competitor_requests: list[CompetitorRequest]
    provider_requests: list[ProviderRequest]
    location_requests: list[LocationRequest]
    total_request_count: int
    cached_request_count: int
    estimated_paid_request_count: int
    estimated_cost_usd: Decimal
```

Rules:

- The planner creates the exact request objects.
- The executor consumes those exact objects.
- The executor must not create hidden extra requests.
- Request fan-out must be visible before execution.
- Seed count, SERP count, backlink count, and provider count must come from the plan.
- Testing and full profiles must have distinct limits.
- Full-profile defaults must not exceed default request limits.

## Acceptance criteria

- Planned request count equals executed request count.
- Tests fail when an adapter attempts an unplanned request.
- Full scans are not blocked by contradictory default limits.
- Testing scans remain within their documented limits.
- The UI shows the exact request graph before execution.

---

## 6. Correct cost attribution

Every API call must be attributed to one scan using `source_scan_run_id`.

Wire the existing `ApiCallORM` or equivalent ledger into all provider calls.

Store:

```text
scan_run_id
provider
endpoint
planned_request_id
cache_status
started_at
completed_at
estimated_cost_usd
actual_cost_usd
provider_task_id
provider_request_id
status
error_type
```

Rules:

- Actual scan cost is the sum of calls linked to that scan.
- Do not calculate cost solely from timestamp ranges.
- Cached responses have zero incremental cost.
- Replay and fixture calls have zero external cost.
- Failed paid calls still record provider cost when applicable.
- Planned-versus-actual differences must be visible.

## Acceptance criteria

- Overlapping scans cannot attribute each other's costs.
- Cost totals reconcile to per-call records.
- Cached and replayed calls show zero incremental cost.
- Tests cover concurrent scans and partial failures.

---

## 7. Preserve actual failure stage

Before marking a scan failed:

1. Save the current stage.
2. Persist it as `failed_stage`.
3. Store the provider endpoint and request ID when available.
4. Preserve partial normalized outputs.
5. Do not replace the latest valid full score.

Required scan states:

```text
queued
resolving_location
planning
discovering_keywords
fetching_metrics
fetching_serps
fetching_competitors
fetching_providers
scoring
completed
partial
failed
cancelled
```

## Acceptance criteria

- A competitor-fetch failure reports `fetching_competitors`.
- A failed rescan leaves the previous valid score active.
- Partial evidence is inspectable.
- Tests cover failure at every stage.

---

## 8. Shorten database transactions

Do not hold a write transaction open while awaiting external calls.

Use this pattern:

```text
persist stage and commit
→ perform external request
→ open transaction
→ persist result and commit
→ move to next stage
```

Requirements:

- Heartbeats use independent short transactions.
- Cancellation can be persisted during an active external call.
- Scan polling is not blocked by a long write transaction.
- File-backed SQLite remains the default development database.
- Do not add Celery, Redis, or another queue in this milestone.

## Acceptance criteria

Add file-backed SQLite tests covering:

- heartbeat while a scan awaits a mocked slow API
- cancellation during a mocked slow API
- stale worker recovery
- two workers attempting to claim the same scan
- status polling during execution

---

# Milestone 2 — Scoring V2

## 9. Replace scoring V1

Create scoring version `v2`.

Required components:

```text
Demand Evidence
Commercial Value
Competitor Weakness
Organic Click Availability
Provider Suitability
Data Completeness
```

The total score should remain 0–100, but all weights and thresholds must be configuration driven.

Suggested starting weights:

```yaml
version: v2

components:
  demand_evidence:
    weight: 20
  commercial_value:
    weight: 15
  competitor_weakness:
    weight: 25
  organic_click_availability:
    weight: 20
  provider_suitability:
    weight: 15
  data_completeness:
    weight: 5
```

These are initial values, not permanent truths.

---

## 10. Scoring direction invariants

Add tests that guarantee:

1. Stronger competitors cannot improve `Competitor Weakness`.
2. More referring domains cannot improve `Competitor Weakness`.
3. Greater exact service relevance among competitors cannot improve `Competitor Weakness`.
4. Greater local relevance among competitors cannot improve `Competitor Weakness`.
5. More ads cannot improve `Organic Click Availability`.
6. A larger or more dominant local pack cannot improve `Organic Click Availability`.
7. More strong directories cannot improve `Organic Click Availability`.
8. More national brands cannot improve `Organic Click Availability`.
9. CPC affects `Commercial Value`, not organic difficulty.
10. Missing a core component prevents high confidence.
11. One minor missing field does not receive the same penalty as several core missing components.
12. Preliminary assessments cannot be directly ranked as full scores.
13. Adding valid evidence cannot reduce data completeness.
14. No score component silently interprets missing as zero unless zero is semantically correct.

---

## 11. Score configuration

Move these values out of Python and into versioned YAML:

- component weights
- search-volume normalization
- CPC normalization
- authority thresholds
- referring-domain thresholds
- local-relevance thresholds
- page-relevance thresholds
- directory penalties
- national-brand penalties
- marketplace penalties
- lead-generator penalties
- local-pack penalties
- ad penalties
- provider count ranges
- provider quality thresholds
- missing-data penalties
- confidence thresholds

Persist the scoring configuration hash with each score.

---

## 12. Score explanation

Every full score must persist:

```text
score version
configuration hash
component name
component availability: available | partial | missing
raw inputs
normalized inputs
formula description
component score
penalties
confidence effect
human-readable explanation
source evidence IDs
source timestamps
```

Example explanation:

```text
Competitor Weakness: 14/25

Negative:
- 4 of the top 5 organic pages are strongly service-specific.
- Median ranking-page referring domains: 42.
- 3 ranking domains exceed the configured authority threshold.

Positive:
- 2 top results are broad directory pages.
- 1 local provider page has weak exact-query relevance.
```

The explanation must be generated from stored facts, not generic boilerplate.

---

## 13. Confidence model

Confidence is separate from attractiveness.

Suggested confidence inputs:

- required component availability
- source mode: fixture, replay, sandbox, live
- data age
- geographic granularity
- number of representative SERPs
- number of competitor records
- provider sample completeness
- normalization warnings
- estimation use

Confidence outputs:

```text
high
medium
low
insufficient
```

Rules:

- Preliminary assessments cannot be high confidence.
- Missing competitor data prevents high confidence.
- National-only demand reduces local-demand confidence.
- Stale SERPs reduce confidence.
- Synthetic fixtures remain visibly synthetic regardless of confidence.

---

# Milestone 3 — Demand modeling

## 14. Separate demand concepts

Persist these separately:

```text
raw_keyword_volume
raw_volume_granularity
national_service_demand
estimated_market_demand
market_estimation_method
market_estimation_confidence
high_intent_keyword_count
clustered_demand
seasonality
demand_source
```

Rules:

- Never label national volume as exact city volume.
- Never imply ZIP-level precision when unavailable.
- Preserve original provider metrics.
- Estimated local demand must be labeled as estimated.
- The scoring formula must know whether it is using raw or estimated demand.

---

## 15. Market-demand estimation

Implement a simple, transparent estimator only if adequate offline geography data is available.

Possible inputs:

- market population
- metro population
- state or national population
- service-specific urban/rural adjustment
- market share of broader geography
- local SERP evidence
- provider density

Do not use a black-box ML model.

Store:

```text
formula version
inputs
output
confidence
limitations
```

If confidence is too low, omit estimated local demand rather than fabricate precision.

---

## 16. Demand-score behavior

Demand Evidence may consider:

- clustered high-intent demand
- number of viable transactional clusters
- seasonality concentration
- market-estimate confidence
- market size
- informational-query ratio

Do not blindly sum close keyword variants.

Add tests showing:

- two identical national-volume opportunities can receive different local-demand confidence
- a smaller market is not treated identically to a major metro without explanation
- low-confidence estimates reduce confidence, not necessarily raw commercial value
- strong national demand alone cannot create a high-confidence local opportunity

---

# Milestone 4 — Keyword pipeline completion

## 17. Service seed structure

Each service family should support:

```yaml
id: water_heater_services
display_name: Water Heater Services

seed_queries:
  - water heater repair
  - water heater replacement

intent_modifiers:
  - emergency
  - repair
  - replacement
  - installation
  - same day

negative_terms:
  - diy
  - jobs
  - salary
  - manual
  - parts

provider_categories:
  - plumber
  - water heater installation service
```

Remove remaining service-specific keyword behavior from shared adapters.

---

## 18. Keyword decision pipeline

Required stages:

```text
seed input
→ generated candidate
→ normalized
→ exact duplicate removal
→ negative filtering
→ metrics retrieval
→ close-variant clustering
→ intent classification
→ value ranking
→ representative SERP selection
```

Persist one decision record per keyword:

```text
original keyword
normalized keyword
cluster ID
decision
decision reason
intent
volume
CPC
granularity
selected for SERP
ranking score
```

Decision values:

```text
included
excluded_negative
excluded_duplicate
grouped_variant
representative
insufficient_data
```

---

## 19. Representative SERP selection

Select representative queries after metrics are available.

Selection should balance:

- transactional intent
- service-family relevance
- search volume
- CPC
- local modifier quality
- cluster coverage
- diversity across service subtypes

Do not select three near-identical queries when three distinct high-value clusters exist.

Persist the selection rationale.

## Acceptance criteria

- Input order does not determine SERP selection.
- Near-identical variants do not consume multiple SERP calls without justification.
- The opportunity report shows why each SERP was purchased or replayed.
- Tests cover cross-cluster selection and tie-breaking.

---

# Milestone 5 — SERP classification

## 20. Required classifications

```text
local_provider
directory
national_brand
marketplace
lead_generator
informational_publisher
government_or_nonprofit
unknown
```

---

## 21. Classifier redesign

Replace overlapping hard-coded branches with explicit ordered rules and configuration.

Store:

```text
classification
classification confidence
classifier version
matched rules
evidence
manual override
override reason
```

Inputs may include:

- configured known-domain lists
- domain patterns
- title and snippet
- URL path
- matching provider listings
- service relevance
- local-market terms
- organization metadata

Rules:

- A domain must not be simultaneously unreachable in multiple categories due to branch order.
- Weak heuristics must not force `local_provider`.
- Ambiguous results remain `unknown`.
- Manual overrides are optional and auditable.
- Overrides do not become required labels for the engine to function.

---

## 22. Classification configuration

Move known domains to versioned configuration:

```yaml
directories:
  - yelp.com
  - angi.com

marketplaces:
  - thumbtack.com

lead_generators:
  - homeadvisor.com

national_brands: []
government_or_nonprofit:
  - .gov
```

Support exact domains and suffix rules.

## Acceptance criteria

- Tests cover overlapping and ambiguous domains.
- `unknown` remains a valid outcome.
- Provider-listing matches improve local-provider confidence.
- Classification changes can trigger offline rescoring.

---

# Milestone 6 — Competitor analysis

## 23. Ranking-page evidence

For each representative SERP, persist evidence for the highest-value organic results:

```text
position
URL
domain
classification
domain authority/rank
page backlinks
page referring domains
domain referring domains
exact service relevance
local relevance
page type
title quality
dedicated page indicator
captured timestamp
```

Do not reduce competitor analysis to domain authority alone.

---

## 24. Relevance scoring

Implement deterministic page-relevance and local-relevance scoring.

Possible signals:

### Service relevance

- exact service phrase in title
- service phrase in URL
- service-specific heading or snippet
- dedicated service page
- broad homepage versus dedicated page
- service subtype match

### Local relevance

- exact city/market in title
- city/market in URL
- service-area evidence
- provider listing located in market
- broad statewide page versus local page

Persist the individual signals, not only the final relevance number.

---

## 25. Competitor Weakness component

The component should consider:

- ranking-page referring domains
- ranking-domain strength
- exact service relevance
- local relevance
- result classification
- number of weak pages in high positions
- directory versus provider composition
- dedicated-page prevalence

A weak result should mean:

- low authority or weak links
- poor query match
- poor local match
- broad directory or generic article
- thin or non-dedicated page

Do not use PageSpeed as a major ranking-difficulty signal.

## Acceptance criteria

- Strong, dedicated local pages lower the component score.
- Weak generic directory pages increase the component score.
- Evidence remains inspectable by result.
- Tests cover mixed SERPs.

---

# Milestone 7 — Provider suitability

## 26. Purpose

The provider analysis should answer:

> Are there several plausible businesses that could eventually become the exclusive tenant for this property?

It should not attempt to determine which provider will definitely pay.

---

## 27. Provider evidence

Persist:

```text
business name
category match
market match
active status
website
phone
email if available
contact form if available
rating
review count
hours if available
service-area evidence
distance from market center
source
data timestamp
```

---

## 28. Provider suitability model

Consider:

- number of relevant active providers
- number of contactable providers
- service-category match
- market coverage
- website presence
- review-count distribution
- rating distribution
- provider concentration
- likely capacity proxies
- data completeness

Avoid simplistic behavior where more providers always means a better market.

Example shape:

```text
0–2 plausible providers      → too few possible tenants
3–12 plausible providers     → healthy range
13–30 plausible providers    → competitive but viable
very high saturation         → possible competition penalty
```

All thresholds must be configurable.

Provider Suitability must not depend on manual labeling.

---

## 29. Provider shortlist display

The discovery UI may show provider candidates, but it must not:

- generate outreach
- create provider configs
- select a tenant
- imply the providers agreed to participate

Show why each provider appears plausible and the limitations of the data.

---

# Milestone 8 — Discovery UI

## 30. Opportunity list

Show:

```text
service family
market
assessment type
total score
confidence
demand evidence
commercial value
competitor weakness
organic click availability
provider suitability
data completeness
data mode
last scanned
data age
scan cost
status
```

Requirements:

- Preliminary and full results must be visually separated.
- Sorting by total score applies only to comparable full scores by default.
- Synthetic and replayed data are clearly labeled.
- Stale data is visible.

---

## 31. Opportunity detail report

Required sections:

### Summary

- score
- confidence
- strongest positives
- strongest risks
- missing evidence
- recommendation status

### Market interpretation

- original location input
- normalized market
- coordinates
- geographic confidence
- keyword granularity
- SERP granularity
- provider granularity

### Demand

- keyword clusters
- representative keyword per cluster
- raw volume
- granularity
- estimated local demand
- seasonality
- excluded and grouped keywords

### SERP composition

- selected queries
- result-type distribution
- ads
- local pack
- directories
- national brands
- marketplaces
- lead generators

### Competitors

- ranked pages
- authority
- referring domains
- service relevance
- local relevance
- classification
- weakness explanation

### Providers

- provider count
- plausible-provider count
- contactable-provider count
- provider evidence
- suitability explanation

### Score breakdown

- component scores
- inputs
- formula version
- penalties
- confidence effects
- evidence links

### Scan metadata

- scan profile
- data mode
- adapter versions
- planned calls
- executed calls
- cache hits
- actual cost
- timestamps
- replay source
- warnings

---

## 32. Score comparison

Add a comparison view for two or more full opportunities.

Compare:

```text
demand
commercial value
competitor weakness
organic click availability
provider suitability
completeness
confidence
data age
cost
```

The comparison must show differences in underlying evidence, not only totals.

---

## 33. Rescoring

Allow rescoring from stored data when:

- scoring configuration changes
- classification rules change
- keyword grouping changes
- provider suitability thresholds change

Requirements:

- no API calls
- new score version saved
- old score preserved
- diff displayed
- reason for score change visible

---

# Milestone 9 — Tests and offline verification

## 34. Required tests

Add tests for:

### Execution

- planner/executor request equality
- unplanned request rejection
- request-limit consistency
- cost attribution by scan ID
- actual failed stage
- concurrent scans
- SQLite heartbeat
- SQLite cancellation
- stale recovery

### Scoring

- every direction invariant
- proportional missing-data penalties
- confidence states
- preliminary/full separation
- historical scoring versions
- rescoring without network calls

### Demand

- national versus local labels
- estimation confidence
- population-based estimation inputs
- no fabricated precision

### Keywords

- exact duplicates
- close variants
- negative filtering
- cluster formation
- representative selection
- cluster diversity

### SERP classification

- directory
- marketplace
- national brand
- lead generator
- local provider
- unknown
- overlapping-domain rules
- manual override audit

### Competitors

- relevance signals
- strong local page
- weak directory page
- mixed SERP
- missing backlink data

### Providers

- too few providers
- healthy range
- saturation
- contactability
- incomplete data

### UI/API

- full opportunity report shape
- preliminary/full visual separation
- score comparison
- rescoring diff
- stale-data warnings

### Network safety

- fixture mode: zero network calls
- replay mode: zero network calls
- rescoring: zero network calls
- CI: zero production API calls

---

## 35. Verification commands

The repository should support:

```bash
make verify
```

and targeted commands similar to:

```bash
uv run pytest tests/unit/scoring
uv run pytest tests/unit/keywords
uv run pytest tests/unit/serp
uv run pytest tests/unit/providers
uv run pytest tests/integration/test_scan_execution.py
uv run pytest tests/e2e/test_discovery_fixture.py
uv run pytest tests/e2e/test_discovery_replay.py
```

Exact paths may vary with repository structure.

---

# Milestone 10 — Documentation

## 36. Update current-state documentation

Update `docs/current_state.md` with:

- completed discovery capabilities
- remaining limitations
- scoring V2 behavior
- demand granularity rules
- execution modes
- cost controls
- replay behavior
- current non-goals

---

## 37. Add discovery documentation

Create:

```text
docs/discovery_architecture.md
docs/scoring_v2.md
docs/demand_model.md
docs/serp_classification.md
docs/provider_suitability.md
docs/discovery_exit_criteria.md
```

Each document must describe actual implemented behavior, not aspirational behavior.

---

# 38. Implementation order

Codex should implement the work in this order:

1. Scan execution correctness
2. Cost attribution and failure diagnostics
3. SQLite transaction/concurrency fixes
4. Scoring V2 framework and invariants
5. Demand granularity and estimation
6. Keyword pipeline completion
7. SERP classification
8. Competitor relevance and weakness analysis
9. Provider suitability
10. Discovery UI/reporting
11. Rescoring and comparison
12. Tests and documentation

Do not begin domain, outreach, site, or deployment work during this implementation.

---

# 39. Discovery exit criteria

The discovery phase is complete when:

1. Planned calls exactly match executed calls.
2. Full scans fit within coherent request limits.
3. API costs are attributed by scan ID.
4. Failed scans preserve the real failed stage.
5. Heartbeat and cancellation work with file-backed SQLite.
6. Cached and replayed scans make no paid calls.
7. Preliminary and full assessments remain separate.
8. Scoring V2 passes all directionality tests.
9. Missing-data penalties are proportional.
10. Confidence is distinct from attractiveness.
11. National demand is not mislabeled as local demand.
12. Local-demand estimates expose method and confidence.
13. Keywords are clustered without blind double-counting.
14. Representative SERPs cover valuable distinct clusters.
15. SERP classification is versioned and explainable.
16. Competitor analysis uses authority, links, service relevance, and local relevance.
17. Provider suitability evaluates plausible future tenants rather than raw count alone.
18. Opportunity detail pages expose all material evidence.
19. Full opportunities can be compared using evidence.
20. Stored data can be rescored without API calls.
21. Fixture and replay end-to-end discovery tests pass.
22. The user can select a candidate opportunity and explain precisely why the engine prefers it.

Real-market validation is intentionally deferred until after further development iterations.

---

# 40. Explicit non-goals

Do not implement:

- domain generation
- domain availability
- provider outreach text
- email sending
- active-provider configuration
- site generation
- cloud deployment
- forms
- phone routing
- analytics
- Search Console
- billing
- pricing recommendations
- live-market validation set
- ML requiring manual labels

The only goal of this specification is to finish the discovery engine.
