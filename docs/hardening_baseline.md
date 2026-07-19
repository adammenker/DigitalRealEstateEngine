# V1 Hardening Baseline

Recorded before V1 hardening edits on branch `v1-hardening-spec`.

- Baseline HEAD: `fb20ba7f6bebebe0a7e6e1cb934b7f58b9a182f0`
- Python: `3.12.3`
- Node: `16.14.2` locally; frontend verification uses Docker `node:20-slim`
- npm: `8.5.0` locally
- Docker: `28.1.1`
- Upstream: `origin/main`

## Repository Structure

- `src/rank_rent`: FastAPI app, CLI, SQLAlchemy ORM, scanner pipeline, adapters, replay, planning, scoring, and site generation.
- `frontend`: Next.js operator dashboard.
- `tests`: offline unit and fixture E2E tests.
- `seeds`: example services and locations.
- `fixtures`: expected fixture outputs.
- `docs` and `specs`: implementation notes and steering specifications.

## Operating Modes

- `fixture`: deterministic synthetic providers, no network calls.
- `replay`: stored DataForSEO responses routed through live normalizers, no network calls.
- `live`: real DataForSEO adapter, gated by credentials and `ALLOW_LIVE_API_CALLS=true`.

## Existing Capabilities

- Raw-response caching exists for DataForSEO requests when a DB session is present.
- Replay transport can read stored DB responses or exported bundles.
- Scan planning estimates uncached live API cost before execution.
- API scans can be queued as in-process background jobs.
- Discovery scans do not generate sites by default.

## Database Behavior

Before this hardening slice, the app used direct SQLAlchemy `create_all()` startup initialization and disposable local DB data. The V1 spec requires Alembic migrations, so this branch reintroduces migrated schema management.

## Verification

The offline verification command is:

```bash
make verify
```

It runs Ruff, mypy, pytest, frontend production build with `npm ci`, and Docker image builds. No verification target makes paid DataForSEO calls.

## Known Baseline Gaps

- Full live qualification is not implemented.
- Scoring was version `v1` at this baseline. Discovery completion has since moved scoring to `v2`.
- Offline geography is limited to existing model fields and a small coordinate lookup.
- Async jobs are in-process and not a durable external worker queue.
- Site/deployment hardening remains approval-gated future work.
