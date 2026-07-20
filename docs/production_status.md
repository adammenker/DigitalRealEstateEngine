# Production Status

Last updated: 2026-07-19

The production-readiness master specification is being implemented from baseline commit
`f63991ae74a5daa733f6633ec6aee55578b0ee88`. Release A is the active target. Production
paid scans and public property deployment are not authorized.

## Baseline

| Item | Recorded value |
|---|---|
| Repository commit | `f63991ae74a5daa733f6633ec6aee55578b0ee88` |
| Project Python target | `>=3.12` |
| Local virtual environment | Python 3.14.6 |
| Host default Python | Python 3.9.6; not suitable for project checks |
| Frontend runtime | Node 20.20.2, npm 10.8.2 through Docker |
| Docker | 28.1.1 |
| Migration head | `b7d2f4a9c6e1` |
| Scoring | `v2.12` |
| Evidence quality | `v1` |
| Service catalog | `2026.07.1` |
| SERP classification | `v2` |
| Market prefilter | `v1` |
| Geography | `us-geography-2024.2`, built 2026-07-20 UTC |
| Python lock | No reproducible application dependency lock at baseline |
| JavaScript lock | `frontend/package-lock.json` present |

## Baseline Verification

The previously committed baseline passed 149 backend tests, strict mypy, Ruff, frontend
lint/build, migration-head validation, Docker rebuild, and browser workflow checks. The
new master-spec run exposed an environment defect: `make verify` invoked the host Python
3.9 interpreter, where Ruff is unavailable. The production implementation must make
`make verify` self-contained and reproducible.

## Runtime Inventory

- Frontend: Next.js operator dashboard, port 8010 in local Compose.
- API: FastAPI, port 8011 in local Compose.
- Worker: durable scan loop currently started inside the API lifespan.
- Persistence: SQLite locally; raw provider responses are stored in database JSON.
- External providers: DataForSEO sandbox/production, Census and GeoNames downloads,
  optional DNS/WHOIS, Pexels, Hunter, OpenAI, and Cloudflare configuration.
- Background work: scan claim, heartbeat, cancellation, stale recovery, and retry loop.
- Generated data: `data/us_geography.sqlite3`, `generated_sites/`, replay fixtures, and
  the local ignored `rank_rent.db`.

## Sensitive Configuration

The baseline reads provider credentials from environment variables or `.env`. `.env`,
local databases, virtual environments, and generated runtime output are ignored by Git.
Secret values must never be returned by APIs, logged, included in images, or passed to
frontend environment variables.

## Network-Capable Paths

- `src/rank_rent/integrations/dataforseo/live.py`: sandbox and production DataForSEO.
- `scripts/build_us_geography.py`: U.S. Census and GeoNames dataset downloads.
- Domain availability adapters: DNS and optional WHOIS provider.
- Future production adapters: OIDC, object storage, email, call tracking, telemetry,
  deployment, Search Console, and analytics.

Health checks, CI, benchmark runs, fixtures, and replay tests must never issue paid
provider calls.

## Data Inventory

- Operator and provider records: production database.
- Raw purchased API responses: moving to immutable blob storage with database lineage.
- Geography/public-data snapshots: versioned local or object-store datasets.
- Provider contact details and future lead PII: restricted production tables.
- Audit events, cost ledgers, scan plans, scores, reviews, and deployment records:
  append-only or history-preserving production records.
- Logs: redacted structured events; raw provider and lead payloads are prohibited.

Machine-readable progress and blockers are maintained in `docs/production_status.yaml`.

