# Current State

Baseline and remediation state notes.

- Branch: `main`
- Original implementation commit SHA: `53ec06c6bcd1cbb08800ee48fa997d970206234a`
- Offline remediation base commit SHA: `c2026b64a9c66e9dd3f65fc7d286fe3691f40362`
- Remediation scope includes:
  - `specs/DigitalRealEstateEngine_CODE_REMEDIATION_STEERING_SPEC.md`
  - `specs/DigitalRealEstateEngine_OFFLINE_REMEDIATION_SPEC.md`

## Repository Summary

- `src/rank_rent/`: FastAPI backend, CLI, SQLAlchemy models, fixture scanner pipeline, scoring, adapters, and static site generation.
- `frontend/`: Next.js dashboard for the scanner UI.
- `config/`: scoring and outreach configuration.
- `data/us_geography.sqlite3`: versioned offline U.S. city/ZCTA discovery index.
- `seeds/`: example service and location seeds.
- `tests/`: focused unit and end-to-end fixture tests.
- `migrations/`: Alembic migration environment for local and Docker DB schema upgrades.
- `Dockerfile`, `Dockerfile.frontend`, `docker-compose.yml`: local backend/frontend containers.

## Verification Commands

The complete local verification suite is:

```bash
make verify
```

Individual checks:

```bash
make backend-check
make frontend-build
make docker-build
```

## Baseline Check Results

Initial baseline before remediation:

- `python3 -m ruff check src tests`: passing.
- `python3 -m pytest -q`: passing.
- `python3 -m mypy src`: failing on missing return annotations and one generic `dict` type.
- Frontend production build: passing in Docker-based Node 20 build.
- `docker compose build`: passing.

After Milestone 0 cleanup:

- `make backend-check`: passing.
- `make frontend-build`: passing, but `npm audit` reports dependency advisories.
- `make docker-build`: passing.
- `make verify`: passing.

After V1 hardening:

- `make backend-check`: passing.
- Backend tests cover fixture/live/replay mode separation, deterministic raw-response cache keys, replay transport misses, corrupted replay bundles, cache expiry and sanitization, scan planning budget blocks, Alembic upgrade, failed live-scan persistence, queued scan reuse, typed scan records, current-data audits, and scan-window replay exports.
- `make frontend-build`: passing on Next.js 16 with a clean production `npm audit --omit=dev`.
- `make docker-build`: passing in the current local verification flow.

After discovery completion:

- Opportunity discovery now stores a `discovery_report` artifact with summary, market interpretation, demand, SERP composition, competitors, providers, score breakdown, and scan metadata.
- Scoring is version `v2` and uses demand evidence, commercial value, competitor weakness, organic click availability, provider suitability, and data completeness.
- DataForSEO live/sandbox requests write per-scan `api_calls` ledger rows for cache hits, completed calls, failures, planned request IDs, provider IDs, and actual cost attribution.
- `POST /api/opportunities/{id}/rescore` reruns scoring from stored scan evidence without provider calls.
- `GET /api/opportunities/compare?ids=1,2` returns comparable latest reports and scores.
- `make verify` passes after discovery completion. Local Node is too old for direct frontend builds, so frontend verification should continue through Docker-backed `make frontend-build`.

## Existing Live Adapters

- `DataForSEOLiveProvider`: live DataForSEO adapter for account checks, standalone provider
  location qualification, keyword suggestions, historical keyword volume, organic SERP
  snapshots, backlinks summaries, and business listings.
- Live scans are guarded by `DATA_MODE=live` and `ALLOW_LIVE_API_CALLS=true` because several DataForSEO endpoints are paid.
- DataForSEO live-mode traffic targets `DATAFORSEO_ENVIRONMENT=sandbox` by default, using `https://sandbox.dataforseo.com/v3/...` for free dummy responses. Production calls require `DATAFORSEO_ENVIRONMENT=production`.
- Live calls are cached in `raw_api_responses` when a DB session is available.
- Live calls are also logged in `api_calls`, including sandbox zero-cost calls and cache hits.
- Live scan plans now include exact request payloads where possible, cache-hit state, and explicit unknown request payloads where later calls depend on purchased upstream results.
- Attached live market scans reserve exactly one unused persisted plan row per call and reject unmatched, exhausted, or concurrently reused requests before opening the HTTP client.
- Market scans resolve against the checked-in offline U.S. geography index before planning.
  Every accepted market carries canonical county, metro, coordinates, population, aliases,
  and a provider-search radius; ambiguous markets require dropdown selection.
- Live planning validates canonical market values against the index and provider discovery
  refuses to run without a verified boundary.
- `LIVE_SCAN_DEPTH=testing` limits paid-call fan-out and produces a preliminary assessment instead of a full ranked score.
- DataForSEO account verification passed during prior smoke checks; current live scans may fail if DataForSEO balance is insufficient.

## Existing Replay Adapters

- `DataForSEOReplayProvider`: reuses the live DataForSEO normalization methods while reading stored responses from replay transport.
- `DatabaseReplayTransport`: reads stored responses from `raw_api_responses`.
- `BundleReplayTransport`: runs exported response bundles through the replay adapter with `rank-rent replay bundle`.
- `rank-rent fixtures validate <bundle>` verifies stored response checksums before replay use.
- Recorded response exports are limited to the requested scan's request/response time window.
- Replay mode makes no network calls and does not require live credentials.

## Existing Mock/Fixture Adapters

- `FixtureMarketResearchProvider`: deterministic local market research fixture adapter.
- `MockDomainAvailabilityProvider`: deterministic domain availability fixture adapter.
- `DNSDomainAvailabilityProvider`: live/replay-mode no-credit DNS signal provider. It treats resolving domains as unavailable and DNS name misses as likely available, but it is not a registrar purchase or trademark check.
- `LocalStagingDeploymentProvider`: local-only staging stand-in.

## Persistence and Jobs

- Scans can run synchronously or as durable queued jobs through the API/UI.
- `GET /api/scans` and `GET /api/scans/{scan_id}` expose scan status, costs, plan calls, and typed record counts.
- The backend process starts a database-backed scan worker that atomically claims queued scans,
  heartbeats running work, recovers stale worker-owned scans, and releases terminal scans.
- Scan outputs are retained as JSON artifacts for UI compatibility and also persisted into typed tables for keyword metrics, SERP snapshots/results, competitor metrics, provider candidates, scan plan calls, scan plans, preliminary assessments, full scores, and score components.
- DataForSEO responses are stored with sanitized payloads, checksums, response shape versions, expiry metadata, provider IDs, and source scan IDs.
- `rank-rent data audit` reports raw response counts, typed record counts, and scan/opportunity status distribution.
- `rank-rent reset-db --confirm` remains available for clean local testing, but normal schema evolution now runs through Alembic.

## Known Broken or Prototype Paths

- Full live qualification reports are not implemented yet; `rank-rent qualify --live` performs a low-cost account and location smoke check.
- Current geography is intentionally U.S. city/ZCTA scoped. Address-level and international
  resolution are deferred product-scope additions, not silent fallbacks.
- Site generation, domain generation, and outreach are no longer part of the default scan pipeline, but full approval workflow actions are not implemented yet.
- Startup initialization runs Alembic migrations for normal file-backed DBs. In-memory SQLite tests still use direct SQLAlchemy table creation.
