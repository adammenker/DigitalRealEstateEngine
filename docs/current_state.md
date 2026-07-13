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
- `seeds/`: example service and location seeds.
- `tests/`: focused unit and end-to-end fixture tests.
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

After offline remediation and backend/data hardening:

- `make backend-check`: passing.
- Backend tests cover fixture/live/replay mode separation, deterministic raw-response cache keys, replay transport misses, scan planning budget blocks, failed live-scan persistence, queued scan reuse, typed scan records, current-data audits, and scan-window replay exports.
- `make frontend-build`: passing on Next.js 16 with a clean production `npm audit --omit=dev`.
- `make docker-build`: passing in the current local verification flow.

## Existing Live Adapters

- `DataForSEOLiveProvider`: live DataForSEO adapter for account checks, Google location resolution, keyword suggestions, historical keyword volume, organic SERP snapshots, backlinks summaries, and business listings.
- Live scans are guarded by `DATA_MODE=live` and `ALLOW_LIVE_API_CALLS=true` because several DataForSEO endpoints are paid.
- Live calls are cached in `raw_api_responses` when a DB session is available.
- Live scan plans now include exact request payloads where possible, cache-hit state, and explicit unknown request payloads where later calls depend on purchased upstream results.
- `LIVE_SCAN_DEPTH=testing` limits paid-call fan-out and produces a preliminary assessment instead of a full ranked score.
- DataForSEO account verification passed during prior smoke checks; current live scans may fail if DataForSEO balance is insufficient.

## Existing Replay Adapters

- `DataForSEOReplayProvider`: reuses the live DataForSEO normalization methods while reading stored responses from replay transport.
- `DatabaseReplayTransport`: reads stored responses from `raw_api_responses`.
- `BundleReplayTransport`: runs exported response bundles through the replay adapter with `rank-rent replay bundle`.
- Recorded response exports are limited to the requested scan's request/response time window.
- Replay mode makes no network calls and does not require live credentials.

## Existing Mock/Fixture Adapters

- `FixtureMarketResearchProvider`: deterministic local market research fixture adapter.
- `MockDomainAvailabilityProvider`: deterministic domain availability fixture adapter.
- `DNSDomainAvailabilityProvider`: live/replay-mode no-credit DNS signal provider. It treats resolving domains as unavailable and DNS name misses as likely available, but it is not a registrar purchase or trademark check.
- `LocalStagingDeploymentProvider`: local-only staging stand-in.

## Persistence and Jobs

- Scans can run synchronously or as queued background jobs through the API/UI.
- `GET /api/scans` and `GET /api/scans/{scan_id}` expose scan status, costs, plan calls, and typed record counts.
- Scan outputs are retained as JSON artifacts for UI compatibility and also persisted into typed tables for keyword metrics, SERP snapshots/results, competitor metrics, provider candidates, and scan plan calls.
- `rank-rent data audit` reports raw response counts, typed record counts, and scan/opportunity status distribution.
- Local DB data is disposable during the testing phase. Use `rank-rent reset-db --confirm` or `docker compose down -v` when the schema changes or when clean test data is preferred.

## Known Broken or Prototype Paths

- Full live qualification reports are not implemented yet; `rank-rent qualify --live` performs a low-cost account and location smoke check.
- Site generation, domain generation, and outreach are no longer part of the default scan pipeline, but full approval workflow actions are not implemented yet.
- `init_db()` creates the current schema directly from SQLAlchemy models. Alembic migrations and old-local-DB compatibility are intentionally out of scope until there is production data worth preserving.
