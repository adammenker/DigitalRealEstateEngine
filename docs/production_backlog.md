# Production Backlog

Last reconciled: 2026-07-20

This backlog contains only work that remains after the production-readiness integration. Completed
behavior is summarized in [production_status.md](production_status.md). Release gates are tracked
in [release_checklist.md](release_checklist.md).

## Next Code Changes

- [ ] Add explicit centralized RBAC policies for property, domain, provider-assignment,
  SiteConfig, compliance, deployment, and rollback mutations.
- [ ] Append immutable actor/target audit events for every privileged property mutation.
- [ ] Add permission-denial and audit-chain tests for those property routes.
- [ ] Register `rank-rent prefilter batch` and `rank-rent prefilter top` as thin CLI wrappers
  around the existing addressable-market service.
- [ ] Design reviewed authenticated lead/outcome HTTP contracts before exposing the internal
  service layer.
- [ ] Schedule retention and call-route health jobs once the production worker identity and alert
  destination are configured.

## Release A Environment Work

- [ ] Rotate the previously shared DataForSEO API password.
- [ ] Select and configure a production OIDC tenant, managed secret store, and Redis service.
- [ ] Provision isolated staging PostgreSQL and S3-compatible object storage.
- [ ] Run PostgreSQL worker/cost-control concurrency tests against staging.
- [ ] Rehearse encrypted PostgreSQL and blob backup/restore; record measured RPO/RTO.
- [ ] Configure telemetry export, dashboards, and paging destinations.
- [ ] Exercise synthetic alerts, provider outage, database outage, stale worker, and cost-breaker
  runbooks in staging.
- [ ] Run the executable DataForSEO production qualification matrix.
- [ ] Reconcile the first real DataForSEO billing export with the internal ledger.
- [ ] Deploy staging through the approval workflow and rehearse rollback.
- [ ] Run load, failure-injection, dependency, image, secret, and application security checks.
- [ ] Complete owner UAT for discovery, review, approval, and evidence export.
- [ ] Sign the Release A checklist.

## Release B Provider Decisions

Do not implement speculative adapters before selecting vendors and approving their security and
privacy contracts.

- [ ] Select email delivery and operator-alert providers.
- [ ] Select a call-tracking/routing provider and complete recording/retention legal review.
- [ ] Select registrar, DNS, and public hosting providers.
- [ ] Select Search Console, analytics, and provider-reported outcome sources.
- [ ] Implement adapters with idempotency, timeouts, retries, health checks, least privilege,
  audit events, and fixture contract tests.
- [ ] Exercise real staging form and call routing without exposing an indexable property.
- [ ] Verify disclosure, claims, consent, privacy, retention, analytics, and rollback.
- [ ] Sign the public-launch and operational-learning gates.

## Empirical Product Work

- [ ] Build labeled production-quality datasets for SERP classification, provider suitability,
  evidence gates, and opportunity outcomes.
- [ ] Validate local-demand estimates against measured local demand.
- [ ] Calibrate scoring thresholds and confidence against ranking, lead, and revenue outcomes.
- [ ] Review catalog coverage as new service families enter the product.
- [ ] Add climate/incidence or new public-data signals only with documented causal models.
- [ ] Keep every scoring change versioned, benchmarked, reviewer-approved, and historically
  reproducible.

## Deliberately Deferred Scope

- International and address-level geography.
- Automatic domain purchase.
- Automatic outreach.
- Fake business profiles, addresses, reviews, credentials, or claims.
- Automatic scoring-weight changes.
- Public deployment before Release A and all property gates pass.
