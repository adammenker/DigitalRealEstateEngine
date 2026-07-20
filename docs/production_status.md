# Production Status

Last updated: 2026-07-20

The production-readiness master specification has been implemented through a broad local
engineering foundation on `main`. The repository is suitable for continued local and sandbox
development, but neither paid production discovery nor public property launch is authorized.
The machine-readable source of truth is
[production_status.yaml](production_status.yaml).

## Executive Status

| Area | Current state |
|---|---|
| Local application | Healthy in Docker Compose |
| Discovery and scoring | Implemented and benchmarked offline |
| Release A | Blocked on remaining code gaps and real-environment validation |
| Property staging | Implemented locally with noindex and compliance gates |
| Release B/public launch | Blocked and not authorized |
| Git | Integrated on `main`; local commits still require operator push |

This status uses these terms:

- **Complete local**: implementation and deterministic local tests are present.
- **Partial local**: useful implementation exists, but a specified code path is still absent.
- **Production validated**: exercised successfully against the selected real environment and
  signed off. No workstream has this status yet.

## Verified Snapshot

| Item | Value |
|---|---|
| Baseline commit | `f63991ae74a5daa733f6633ec6aee55578b0ee88` |
| Integrated head before this documentation commit | `4244479c91dd26a4a3b87aef778f360b3c2b2c17` |
| Python source files | 100 |
| Migration files/head | 22 / `d4a7c2e9f1b6` |
| Scoring | `v2.12` |
| Evidence quality | `v1` |
| Service catalog | `2026.07.1` |
| SERP classification | `v2` |
| Addressable market | `addressable-market-v2.0` |
| DataForSEO adapter | `dataforseo-v3-workstream-d-2` |
| Geography | `us-geography-2024.2` |

The latest local verification completed with:

- Backend: 292 passed, 3 skipped, one third-party Starlette deprecation warning.
- Calibration: 26/26 scenarios, 12/12 pairwise expectations, 12/12 SERP labels,
  15/15 provider checks, and zero network attempts.
- Ruff, strict mypy over 100 source files, frontend lint/build, and Docker image builds: passed.
- Local PostgreSQL, API, worker, and frontend containers: healthy.
- Alembic: one linear head at `d4a7c2e9f1b6`.

The three skipped tests are the optional PostgreSQL concurrency tests when
`TEST_POSTGRESQL_URL` is not supplied. The test harness exists, but a hosted-environment run
remains a Release A gate.

## Workstream Summary

| ID | Workstream | Status | Remaining gate |
|---|---|---|---|
| A | Calibration | Complete local | Real outcome calibration |
| B | Public data | Partial local | Register exact CLI commands; activate reviewed datasets |
| C | Production data | Complete local | Hosted PostgreSQL/blob backup and restore rehearsal |
| D | Worker/cost controls | Complete local | Real qualification and provider billing reconciliation |
| E | Security | Partial local | Property RBAC/audit coverage and real OIDC/Redis/secrets |
| F | Observability | Complete local | Real telemetry, alerts, and exercises |
| G | Deployment | Complete local | Provision and rehearse staging/production |
| H | Opportunity review | Complete local | Owner UAT |
| I | Property workflow | Complete local | Security gap resolution and real provider selection |
| J | Lead operations | Partial local | Authenticated APIs and real delivery/call adapters |
| K | Outcome feedback | Partial local | Real source adapters and outcome history |
| L | Independent QA | Partial | Staging load, failure, security, recovery, rollback, and UAT |

## Immediate Code Gaps

These items can be completed without enabling paid traffic:

1. Add centralized permission rules for every property mutation. Production authentication is
   fail-closed, but the mutation-policy table does not yet assign property-specific permissions.
2. Append hash-linked audit events for privileged property, domain, compliance, deployment, and
   rollback actions.
3. Register `rank-rent prefilter batch` and `rank-rent prefilter top`; the underlying service
   and standalone script already exist.
4. Design authenticated lead/outcome HTTP contracts before exposing the existing internal
   service layer.

## External Production Gates

These require operator-selected accounts or infrastructure:

1. Rotate the previously shared DataForSEO test credential.
2. Run the executable production DataForSEO qualification and reconcile real billing.
3. Provision managed PostgreSQL, S3-compatible object storage, Redis, OIDC, secrets,
   telemetry, and alert destinations.
4. Rehearse encrypted backup/restore, deployment/rollback, incident alerts, and disaster
   recovery in staging.
5. Select email, call-routing, registrar, DNS, hosting, and outcome-source providers before
   implementing their production adapters.
6. Complete security review, load/failure testing, owner UAT, and Release A sign-off.
7. Gather real ranking, lead, and revenue outcomes before changing calibrated score weights.

## Authorization

The default fixture and DataForSEO sandbox workflows remain appropriate for testing. Production
DataForSEO requests remain fail-closed behind explicit switches, qualification, spend controls,
and reconciliation. Public deployment remains local-only and fail-closed. See
[release_checklist.md](release_checklist.md) for the exact gate state and
[production_handoff.md](production_handoff.md) for the recommended resume order.
