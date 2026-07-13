# Current State

Baseline recorded before remediation work.

- Branch: `main`
- Commit SHA: `53ec06c6bcd1cbb08800ee48fa997d970206234a`
- Remediation scope started from: `specs/DigitalRealEstateEngine_CODE_REMEDIATION_STEERING_SPEC.md`

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

## Existing Live Adapters

- `DataForSEOLiveProvider`: live DataForSEO adapter for account checks, Google location resolution, keyword suggestions, historical keyword volume, organic SERP snapshots, backlinks summaries, and business listings.
- Live scans are guarded by `DATA_MODE=live` and `ALLOW_LIVE_API_CALLS=true` because several DataForSEO endpoints are paid.
- Live DataForSEO smoke check currently reaches the API, but DataForSEO returns HTTP 403 until the account is verified in the DataForSEO user panel.

## Existing Mock/Fixture Adapters

- `FixtureMarketResearchProvider`: deterministic local market research fixture adapter.
- `MockDomainAvailabilityProvider`: deterministic domain availability fixture adapter.
- `UnknownDomainAvailabilityProvider`: live-mode placeholder that records domain availability as unknown instead of mixing fake domain results into live scans.
- `LocalStagingDeploymentProvider`: local-only staging stand-in.

## Known Broken or Prototype Paths

- Live domain availability checks are not implemented yet.
- Full live qualification reports are not implemented yet; `rank-rent qualify --live` performs a low-cost account and location smoke check.
- Scans still run synchronously.
- Core scan artifacts are still mostly stored as JSON artifacts rather than fully typed migrated tables.
- Site generation still happens during scan in the prototype pipeline.
- Frontend dependency audit reports advisories that require a later dependency/security pass.
