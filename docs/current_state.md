# Current State

## Opportunity review

Opportunity discovery now feeds a validated, attributable review workflow with
ownership, approval completeness, reversible evidence overrides, saved
discovery templates, aggregate-cost-bounded batch testing, and JSON/CSV
evidence packets. No downstream property decision or routing profile may be
created before the opportunity reaches `approved_for_property`.

See [opportunity_review.md](opportunity_review.md) for the state graph and API
contract.

The engine has a complete discovery workflow for evaluating configured home-service
opportunities in canonical U.S. cities and ZIP Code Tabulation Areas (ZCTAs).

## Discovery Workflow

1. Select a service from the versioned catalog or create an explicit draft for testing.
2. Resolve the market through the offline U.S. geography index.
3. Optionally rank markets with the zero-cost public-data prefilter.
4. Run a per-scan `testing` or `full` DataForSEO profile.
5. Evaluate evidence quality before exposing a score.
6. Review the latest assessment, score history, freshness, and API-call ledger.
7. Promote a valid live testing scan to a full scan after reviewing its incremental plan
   and confirming any cost.
8. Compare only rankable full assessments.

Configured services provide stable IDs, aliases, seed queries, intent modifiers, negative
terms, and provider categories. Draft services are intentionally limited to testing scans;
full scans and promotions require a configured service.

## Evidence And Scoring

- Scoring configuration is version `v2.12`.
- Testing scans produce preliminary assessments. Only completed full assessments that do not
  fail the evidence gate can participate in ranking and comparison.
- The evidence-quality gate validates service relevance, representative queries, provider
  relevance, geographic relevance, competitor coverage, and unknown SERP share. Failed
  evidence is marked unusable and score-capped.
- Rescoring uses stored evidence without provider calls and records a reason, timestamp,
  component differences, and total-score difference.
- Opportunity responses expose the latest assessment separately from typed score history.
- Discovery reports include component-specific calculations, missing evidence, confidence,
  source mode, and per-evidence-group freshness.
- Competitor evidence preserves SERP query/position observations and distinguishes page-
  scoped metrics from domain-scoped metrics. Unavailable page metrics remain explicitly
  unavailable rather than being inferred from domain data.

## Geography And Public Data

- `data/us_geography.sqlite3` is the versioned offline index for U.S. populated places and
  ZCTAs.
- Accepted markets carry canonical IDs, county, metro, coordinates, population, aliases,
  source provenance, and a provider-search boundary.
- Ambiguous fuzzy matches require user selection. Unsupported or unresolved markets cannot
  proceed to provider discovery.
- The versioned public-data prefilter ranks canonical markets without DataForSEO calls using
  ACS household, housing-unit, homeownership, and housing-age signals.
- Prefilter evidence is persisted and reported but does not inflate the opportunity score.
- Population-share local-demand estimates remain low-confidence evidence pending empirical
  calibration and richer market factors.

## API Planning And Cost Control

- Scan profile is immutable per scan and does not depend on a mutable global after planning.
- Every attached live market-scan request must consume one unique persisted plan entry before
  a network request can begin.
- Testing-to-full promotion displays the additional uncached requests and estimated cost and
  requires explicit confirmation when cost is nonzero.
- The reconciled ledger joins planned and executed calls by planned request ID and exposes
  planned, executed, cached, failed, unexecuted, unexpected, and actual-cost totals.
- Sandbox remains the default DataForSEO environment. Production traffic requires explicit
  configuration, all applicable kill switches, current adapter-version qualification, and clean
  billing reconciliation.
- Cache misses reserve durable daily request/spend counters transactionally before network access.
  Counters include production/testing totals, endpoint spend, cache misses, unexpected calls,
  provider failures, and schema drift.

## Persistence And Replay

- Queued scans run in a dedicated worker process with configurable concurrency, atomic lease-token
  claims, heartbeat/expiry, cancellation, exponential retry with jitter, stale recovery, and poison
  job quarantine. FastAPI does not run an embedded worker.
- Production configuration requires PostgreSQL and applies explicit pool, statement-timeout,
  idle-transaction-timeout, health, and schema-readiness policies. SQLite remains supported
  for local fixtures and lightweight replay.
- Web requests and workers create sessions from separate factories and never share a Session.
- Typed records cover plans, calls, keywords, SERPs, competitors, providers, preliminary
  assessments, full scores, score components, and public-data prefilter assessments.
- New live-provider raw responses are sanitized and stored as immutable filesystem or optional
  S3-compatible blobs. PostgreSQL stores checksummed blob metadata and source-scan lineage;
  legacy inline rows remain readable and replay/export validates integrity.
- Fixture and replay modes make no network calls.
- A realistic zero-network replay covers the full discovery path, including relevant
  keywords, multiple SERPs, competitor evidence, providers, scoring, reporting, and rescore.

## Repository And Verification

- `src/rank_rent/`: FastAPI backend, discovery services, adapters, CLI, and persistence.
- `frontend/`: Next.js discovery dashboard.
- `config/`: service catalog, evidence-quality, scoring, classification, and prefilter rules.
- `tests/`: unit, API, migration, replay, and end-to-end coverage.
- `migrations/`: Alembic schema history.
- `Dockerfile`, `Dockerfile.frontend`, and `docker-compose.yml`: local container runtime.

Run the complete local verification suite with:

```bash
make verify
```

The 2026-07-20 integrated snapshot passed 292 backend tests with 3 optional PostgreSQL tests
skipped, all 26 calibration scenarios, strict mypy over 100 source files, Ruff, frontend
lint/build, Docker image builds, and local Compose health checks. Alembic has one head at
`d4a7c2e9f1b6`.

## Property, Lead, And Outcome Workflows

- Approved opportunities can create versioned provider-independent properties.
- Domain selection, manual registration evidence, DNS verification, SiteConfig, assets,
  provider assignments, deterministic builds, compliance, deployment records, and rollback are
  implemented locally.
- Preview and staging are always noindex. Public deployment and automatic domain purchase fail
  closed.
- Lead routing and outcomes have durable, privacy-aware internal service layers with fixture
  delivery, call, alert, and source adapters.
- There is no reviewed public lead endpoint or real email, call-routing, hosting, registrar, or
  outcome-source adapter.

## Remaining Production Work

The current source of truth is `docs/production_status.yaml`. Immediate code gaps are centralized
RBAC and append-only audit coverage for property mutations, exact prefilter CLI registration, and
reviewed HTTP contracts for lead/outcome services. Production validation still requires real
OIDC/Redis/secrets, hosted PostgreSQL and blob backup/restore, DataForSEO qualification and billing
reconciliation, telemetry/alerts, deployment/rollback rehearsal, security/load/failure testing,
owner UAT, and empirical calibration. See `docs/production_backlog.md`.
