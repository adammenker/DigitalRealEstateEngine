# Production Backlog

This file tracks work that should be completed before the Digital Real Estate Engine is treated as production-ready. Items here are intentionally practical: each one should reduce bad scans, wasted API spend, operational risk, or misleading opportunity decisions.

## Geocoding And Market Resolution

- [ ] Run Pelias as an optional Docker Compose profile with persistent volumes, health checks, and documented import/update commands.
- [ ] Decide the first production geography scope: US-only, US plus Canada, or broader international.
- [ ] Import the matching Pelias datasets for that scope, likely Who's On First for administrative areas, OpenStreetMap for places, and OpenAddresses/postal data where needed.
- [ ] Add a provider-location concordance job that maps selected Pelias places to DataForSEO `location_code` / `location_name` values and caches those mappings.
- [ ] Add an admin UI or CLI command to inspect unresolved market mappings before spending DataForSEO calls.
- [ ] Add strict country/state validation so provider matches cannot silently cross from `US` to `GB` or any other mismatched country.
- [ ] Improve ZIP-code resolution with city/state, lat/lng, and DataForSEO location mapping.
- [ ] Add automated tests for ambiguous city names such as London, Springfield, Portland, and Columbus.
- [ ] Add a user-facing "unsupported geography" state for locations outside the configured production scope.

## DataForSEO Cost Controls

- [ ] Keep sandbox as the default testing environment and require explicit production environment opt-in.
- [ ] Add per-scan and per-day request/cost budgets surfaced in the UI.
- [ ] Store estimated versus actual API call counts by stage and display them per scan.
- [ ] Add cache warming for stable reference data such as provider locations.
- [ ] Add a "full scan" confirmation step that lists the additional calls required beyond the testing profile.
- [ ] Add alerts for repeated cache misses, unexpected paid endpoints, or provider responses with nonzero cost in testing.

## Data Quality And Scoring

- [ ] Promote preliminary scores in the UI separately from full scores so users do not confuse incomplete sandbox/testing scans with production-grade opportunities.
- [ ] Add confidence bands based on source mode: fixture, sandbox, replay, and production live.
- [ ] Add relevance validation for sandbox/provider responses so obviously unrelated results are labeled as mock/noisy.
- [ ] Improve competitor metric collection before promoting opportunities to full review.
- [ ] Add score explainability panels that show which fields were missing and how much each missing component affected the score.

## Operations

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
