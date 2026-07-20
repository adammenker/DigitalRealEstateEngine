# Production Handoff

Last updated: 2026-07-20

## Resume Here

The production-readiness implementation is integrated on `main` through
`4244479c91dd26a4a3b87aef778f360b3c2b2c17`, plus the documentation/status commit containing
this file. The branch has local commits that the operator intends to push manually.

Before starting new work:

```bash
git status --short --branch
git log --oneline -20
docker compose ps
make verify
```

Read these files in order:

1. [production_status.md](production_status.md) for the human summary.
2. [production_status.yaml](production_status.yaml) for structured workstream state.
3. [release_checklist.md](release_checklist.md) for release blockers.
4. [production_backlog.md](production_backlog.md) for implementation order.
5. [production_deviations.md](production_deviations.md) for intentional boundaries.

## Integrated Scope

The following implementation tracks are on `main`:

- Offline calibration, pairwise invariants, and classification/provider benchmarks.
- Addressable-market public-data profiles, Census adapters, immutable releases, and rollback.
- PostgreSQL policy, separate workers, immutable blob storage, and linear migrations.
- Planned-call enforcement, durable limits, qualification, circuit breakers, and billing
  reconciliation.
- OIDC/RBAC foundations, audit storage, web security, structured telemetry, alerts, CI/CD,
  Terraform, release manifests, and rollback automation.
- Opportunity review, overrides, templates, bounded batches, exports, and approval gates.
- Provider-independent property/domain/SiteConfig/build/compliance/deployment workflows.
- Durable lead routing and source-attributed outcome/calibration service layers.

Latest verified results:

```text
Backend: 292 passed, 3 skipped
Calibration: 26/26 scenarios, 12/12 pairwise, 12/12 SERP, 15/15 provider
Ruff: pass
Strict mypy: pass across 100 source files
Frontend lint/build: pass
Docker image build: pass
Local Compose database/API/worker/frontend: healthy
Alembic: one head at d4a7c2e9f1b6
```

The skipped tests require `TEST_POSTGRESQL_URL`; run them against a disposable hosted
PostgreSQL database before Release A.

## Recommended Resume Order

1. Close the property security gap:
   - add centralized permissions for all property mutations;
   - append audit events for domain, provider, SiteConfig, compliance, deployment, and rollback
     actions;
   - add role and audit tests.
2. Register the exact `rank-rent prefilter batch/top` CLI commands.
3. Select a staging cloud and OIDC provider, then provision the Terraform stack.
4. Run hosted PostgreSQL concurrency and backup/restore/blob-survival rehearsals.
5. Run DataForSEO production qualification and reconcile the first real billing export.
6. Exercise staging telemetry, alerts, failure injection, deployment, rollback, and owner UAT.
7. Sign Release A before selecting public property vendors or enabling public deployment.
8. Select email, call-routing, registrar, hosting, and outcome providers, then implement only
   their reviewed adapters.
9. Collect real outcomes and use them for empirical calibration without automatic weight changes.

## Known Boundaries

- Local Compose is healthy, but it is not a production environment.
- Production authentication code exists, but no real OIDC tenant has been validated.
- Property APIs are authenticated by the global middleware, but their mutation permissions and
  audit coverage are incomplete.
- Lead routing and outcomes are internal service layers with fixture adapters; they are not public
  production APIs.
- Property “production” deployment currently materializes a local production-shaped artifact.
  There is no approved registrar or public hosting adapter.
- Production DataForSEO remains fail-closed until executable qualification and billing gates pass.
- Scores remain decision-support signals and require real-world calibration.

## Historical Delegation

The original work was split across workstreams A-L in temporary worktrees and then integrated
onto `main`. The old per-workstream handoff documents are historical implementation records, not
current status sources. Use the production status and release checklist above when they disagree.
