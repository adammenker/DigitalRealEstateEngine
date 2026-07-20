# Production Backlog

This file tracks work that should be completed before the Digital Real Estate Engine is treated as production-ready. Items here are intentionally practical: each one should reduce bad scans, wasted API spend, operational risk, or misleading opportunity decisions.

## Geocoding And Market Resolution

- [x] Set the first production geography scope to U.S. populated cities and Census ZCTAs.
- [x] Build a versioned offline index with city, state, ZIP, county, metro, coordinates,
  population, reference population, aliases, and source provenance.
- [x] Require exact canonical geography before live scan planning.
- [x] Require explicit selection for ambiguous and fuzzy city matches.
- [x] Reload selected records by canonical ID instead of trusting client coordinates.
- [x] Persist canonical geography fields on markets and preserve them in scan payloads.
- [x] Reject unsupported countries and unknown/non-geographic ZIPs.
- [x] Require a verified coordinate radius for every provider-discovery request.
- [x] Remove hard-coded city coordinates and paid runtime geography resolution.
- [x] Add automated tests for ambiguous city names such as London.
- [x] Expand automated ambiguous-city tests to Springfield, Portland, and Columbus.
- [x] Add a user-facing unsupported-geography response outside the U.S. scope.
- [ ] Add address-level or international geography only when the product scope requires it;
  evaluate Pelias then rather than operating it for current city/ZIP discovery.

## DataForSEO Cost Controls

- [ ] Keep sandbox as the default testing environment and require explicit production environment opt-in.
- [ ] Add per-scan and per-day request/cost budgets surfaced in the UI.
- [ ] Store estimated versus actual API call counts by stage and display them per scan.
- [ ] Add provider-location-code concordance only if inferred canonical names prove unreliable;
  it is not required for the current offline geography boundary.
- [ ] Add a "full scan" confirmation step that lists the additional calls required beyond the testing profile.
- [ ] Add alerts for repeated cache misses, unexpected paid endpoints, or provider responses with nonzero cost in testing.

## Data Quality And Scoring

- [x] Label country-level keyword volume as national demand evidence instead of exact local demand.
- [x] Add service-configured intent modifiers and negative product terms.
- [x] Add close-variant keyword clustering and prevent grouped variants from inflating demand.
- [x] Select representative SERP queries after keyword metrics using value/intent ranking.
- [x] Persist keyword inclusion, exclusion, grouping, and representative-selection decisions.
- [ ] Promote preliminary scores in the UI separately from full scores so users do not confuse incomplete sandbox/testing scans with production-grade opportunities.
- [x] Add confidence bands based on source mode: fixture, sandbox, replay, and production live.
- [ ] Add relevance validation for sandbox/provider responses so obviously unrelated results are labeled as mock/noisy.
- [ ] Improve competitor metric collection before promoting opportunities to full review.
- [ ] Add score explainability panels that show which fields were missing and how much each missing component affected the score.
- [x] Isolate market-demand estimation behind a configured, versioned strategy with
  factor-level inputs, output, confidence, and limitations.
- [ ] Enrich a future offline geography version with validated household, housing-unit,
  homeownership, housing-age, and climate signals before introducing a richer estimator.
  Keep population-share estimates low confidence until that replacement is validated.

## Operations

- [x] Add basic scan cancellation and retry API hooks with UI controls.
- [x] Add a database-backed durable scan worker with atomic claim, heartbeat, stale recovery, and idempotent active retries.
- [ ] Add production database selection and backup/restore workflow.
- [ ] Add structured logs for every scan stage with scan ID, opportunity ID, provider, cache status, and cost.
- [ ] Add health checks for backend, frontend, database, DataForSEO credentials, and optional Pelias.
- [ ] Add a one-command local reset for test data.
- [ ] Add CI that runs backend tests, frontend lint/build, and migration checks.

## Security And Secrets

- [ ] Move all API credentials to local `.env` or a secret manager and never commit secrets.
- [ ] Add startup validation that redacts secrets in logs and API responses.
- [ ] Add explicit production-mode warnings when real API spend is enabled.
- [ ] Add least-privilege guidance for external providers such as Cloudflare and DataForSEO.

## Product Workflow

- [ ] Add saved scan templates for repeated service/market tests.
- [ ] Add opportunity statuses for rejected, needs data, ready for outreach, site generated, and launched.
- [ ] Add CSV/JSON export of scan evidence.
- [ ] Add review screens for provider candidates, domains, and generated site assumptions before launch.
