# Digital Real Estate Engine

Local-first opportunity discovery and underwriting for rank-and-rent local lead-generation
markets.

The current application evaluates a configured home-service family in a canonical U.S. city
or ZIP market. It combines public market data, keyword demand, SERP composition, competitor
strength, organic-click availability, and potential tenant suitability into an explainable
assessment.

## Project Status

The discovery engineering workflow is implemented end to end:

- Versioned service catalog with authoritative keyword and provider-category rules.
- Offline U.S. city and ZCTA search with fuzzy suggestions and explicit selection.
- Zero-cost public-data market prefilter using household and housing signals.
- Per-scan `testing` and `full` research profiles.
- Durable background scans with cancellation, retry, heartbeat, and stale-job recovery.
- Exact API planning, unique request consumption, cache tracking, and cost reconciliation.
- Evidence-quality gates that prevent noisy scans from becoming ranked opportunities.
- Preliminary-to-full promotion with lineage, incremental call preview, and cost confirmation.
- Explainable scoring, evidence freshness, historical rescoring, and full-opportunity comparison.
- Fixture and realistic DataForSEO replay coverage that makes no network calls.
- PostgreSQL production configuration with bounded pooling/timeouts and separate web/worker
  sessions, while retaining SQLite for local and replay use.
- Immutable raw-response blob storage through a filesystem adapter or optional S3-compatible
  adapter, with checksummed database metadata and source-scan lineage.

Scoring currently uses configuration version `v2.12`. The architecture is ready for controlled
discovery testing, but the scores and quality thresholds still require empirical calibration
against production-quality evidence and real business outcomes before production use.

See [Current State](docs/current_state.md), [Discovery Exit Criteria](docs/discovery_exit_criteria.md),
and the [Production Backlog](docs/production_backlog.md) for more detail.

## Run The Application

Docker Compose is the primary local runtime:

```bash
docker compose up -d --build
```

Open:

- Engine dashboard: [http://localhost:8010](http://localhost:8010)
- Backend API and debugging: [http://localhost:8011](http://localhost:8011)
- Interactive API documentation: [http://localhost:8011/docs](http://localhost:8011/docs)

Both services use `restart: unless-stopped`, so they remain running and restart with Docker
Desktop. SQLite data and filesystem raw-response blobs are stored in the `rank_rent_data`
Docker volume.

Useful commands:

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose up -d --build
docker compose down
```

Use different host ports when necessary:

```bash
RANK_RENT_PORT=8020 RANK_RENT_BACKEND_PORT=8021 docker compose up -d --build
```

## Use The Dashboard

1. Select an authoritative service or explicitly use typed text as a testing-only draft.
2. Enter a U.S. city, state, or ZIP and select a canonical result from the dropdown.
3. Optionally choose **Find markets** to run the zero-cost public-data prefilter.
4. Select the **Testing** or **Full** scan profile.
5. Keep **Dry run** enabled to inspect the exact request plan without executing it.
6. Disable **Dry run** to run or queue the scan.
7. Review evidence quality, component calculations, freshness, providers, and the API ledger.
8. Promote an eligible live testing assessment after reviewing its additional full-scan cost.
9. Rescore stored evidence without API calls or compare two to four rankable full assessments.

Testing scans are preliminary and never replace the latest ranked full score. Failed evidence
is marked unusable, score-capped, and excluded from ranking, comparison, and promotion.

## Data Modes And API Safety

The default mode is deterministic fixture data:

```env
DATA_MODE=fixture
ALLOW_LIVE_API_CALLS=false
```

Fixture and replay modes do not make network calls. Replay runs stored DataForSEO-shaped
responses through the same normalization and scoring pipeline used by live scans.

To test the live adapter without paid calls, create a local `.env` from `.env.example` and set:

```env
DATA_MODE=live
ALLOW_LIVE_API_CALLS=true
DATAFORSEO_ENVIRONMENT=sandbox
DATAFORSEO_LOGIN=your-api-login
DATAFORSEO_PASSWORD=your-api-password
```

Sandbox is the default DataForSEO environment and targets
`https://sandbox.dataforseo.com/v3/...`. Production requests require all of the following:

```env
DATA_MODE=live
ALLOW_LIVE_API_CALLS=true
DATAFORSEO_ENVIRONMENT=production
```

Production also requires valid credentials. Never commit `.env` or credentials.

Live market scans cannot silently exceed their persisted plan: every external request must
consume one unique planned request before network access. The dashboard shows estimated and
actual calls, cache hits, failures, unexpected calls, and provider-reported cost.

For a private production deployment, set `APP_ENV=production` and provide a PostgreSQL
`DATABASE_URL`; production startup rejects SQLite. Configure durable raw-response storage with
`BLOB_STORE_BACKEND=filesystem` on a separately backed-up volume, or install `.[s3]` and use an
S3-compatible bucket. See [Production Database and Storage](docs/production_data.md).

## Data And Resetting

Backend startup applies Alembic migrations automatically. The checked-in geography database is
`data/us_geography.sqlite3`; the mutable application database lives in the Docker volume.

Delete all local test application data:

```bash
docker compose down -v
docker compose up -d --build
```

For a non-Docker backend environment:

```bash
rank-rent reset-db --confirm
```

Validate a recorded replay bundle:

```bash
rank-rent fixtures validate path/to/bundle.json
```

## Development And Verification

Requirements:

- Python 3.12 or newer
- Node.js 20.9 or newer
- Docker Desktop for the standard full-stack workflow

Install the backend development environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the complete verification flow:

```bash
make verify
```

Individual checks:

```bash
python -m ruff check src tests scripts
python -m mypy --strict src
python -m pytest -q

cd frontend
npm ci
npm run lint
npm run build
```

GitHub Actions runs backend lint, strict typing, migrations, tests and replay coverage,
frontend lint/build, and Docker image builds. Verification never enables production API calls.

Run queued scans in a process separate from the API:

```bash
rank-rent worker --concurrency 2
```

Paid-call limits, kill switches, qualification expiry, billing reconciliation, and worker retry /
quarantine operations are documented in `docs/workstream_d_operations.md`.

## Repository Layout

- `src/rank_rent/`: FastAPI API, durable scan worker, provider adapters, scoring, and persistence.
- `frontend/`: Next.js discovery dashboard.
- `config/services.yaml`: authoritative service catalog.
- `config/scoring.yaml`: versioned scoring and confidence configuration.
- `config/evidence_quality.yaml`: semantic evidence-gate thresholds.
- `config/market_prefilter.yaml`: zero-cost public-data market-ranking configuration.
- `data/us_geography.sqlite3`: offline U.S. populated-place and ZCTA index.
- `migrations/`: Alembic schema history.
- `tests/`: unit, API, migration, replay, and end-to-end coverage.
- `docs/`: architecture, model behavior, current state, and production backlog.

## Production Boundary

This project does not claim that an opportunity is guaranteed to rank, generate leads, or
become profitable. Assessments are research aids with explicit inputs, assumptions, confidence,
missing-data effects, and evidence-quality labels.

Before production use, the project still needs labeled real-world calibration, validated local
demand improvements, a deployed PostgreSQL concurrency test and backup/restore rehearsal,
external observability and alert delivery, authentication and secret management, and downstream
review/launch workflows. Workstream D's durable spend controls remain fail-closed until a current
qualification matrix and, after the first production charge, clean billing reconciliation exist.
