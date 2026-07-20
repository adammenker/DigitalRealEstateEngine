# Release Checklist

Last reconciled: 2026-07-20

Checked items are implemented and locally verified. Unchecked items are real release blockers.
A checked local contract does not substitute for staging or production rehearsal.

## Gate A: Calibration Ready

- [x] Benchmark harness and at least 20 scenarios pass.
- [x] Pairwise business-direction invariants pass.
- [x] Classification and provider benchmarks pass.
- [x] Decision-affecting configuration is versioned and validated.
- [ ] Evidence gates, thresholds, and scores are calibrated against production-quality samples.

**State:** Local benchmark gate passed; empirical calibration remains open.

## Gate B: Paid Scan Ready

- [x] Every attached market-scan call consumes one unique planned request.
- [x] Per-scan and daily limits stop calls before network access.
- [x] Kill switches, circuit breakers, and synthetic cost alerts pass locally.
- [x] Billing reconciliation code and mismatch handling pass fixture tests.
- [ ] DataForSEO production qualification is current.
- [ ] A real provider billing export reconciles with the internal ledger.
- [ ] Previously shared DataForSEO credentials are rotated.

**State:** Paid production scans are blocked.

## Gate C: Internal Production Ready

- [x] PostgreSQL concurrency test harness is implemented.
- [x] Immutable filesystem and S3-compatible blob contracts are tested.
- [x] Fail-closed OIDC, RBAC, audit storage, web controls, and secret references are implemented.
- [x] Structured logs, metrics, trace context, dashboards, alerts, and runbooks are implemented.
- [x] Approval-gated CI/CD, release manifests, Terraform, and rollback automation are implemented.
- [x] Opportunity review and approval workflows pass automated tests.
- [ ] Every property mutation has an explicit centralized permission.
- [ ] Every privileged property action writes an append-only audit event.
- [ ] Hosted PostgreSQL concurrency tests pass.
- [ ] Blob persistence survives a rehearsed production-like database restore/reset.
- [ ] Backup and restore rehearsal meets RPO/RTO targets.
- [ ] Real OIDC, Redis, managed secrets, telemetry, and alerts are active in staging.
- [ ] Deployment and rollback rehearsal passes in staging.
- [ ] Opportunity review workflow passes owner UAT.
- [ ] Security scans and review have no unresolved critical findings.

**State:** Release A is not approved.

## Gate D: Property Staging Ready

- [x] An `approved_for_property` opportunity is required.
- [x] SiteConfig and provider assignments are versioned.
- [x] Compliance review is required.
- [x] Preview/staging builds are deterministic and always noindex.
- [x] Routing health is required by the release service.
- [ ] Property-route RBAC and audit gaps are closed.
- [ ] The workflow is exercised in the selected staging environment.

**State:** Local property staging is implemented; deployed staging is not approved.

## Gate E: Public Launch Ready

- [x] Referral disclosure, claim attribution, metadata, sitemap, and indexing rules are enforced
  by the builder.
- [x] Domain purchase and public deployment fail closed without explicit operator evidence.
- [x] Provider replacement preserves the property asset and tracking-number ownership model.
- [ ] Release A is signed off.
- [ ] A domain is manually registered, operator-approved, and DNS-verified.
- [ ] A production provider assignment and all public claims are approved.
- [ ] Real forms, calls, alerts, analytics, privacy, and retention are verified.
- [ ] A reviewed public hosting adapter is configured.
- [ ] Production rollback is rehearsed.

**State:** Public launch is not authorized.

## Gate F: Operational Learning Ready

- [x] Search/lead outcome models link to original evidence and score versions.
- [x] Observed, operator-verified, inferred, and provider-reported truth remain distinct.
- [x] Historical rescoring preserves the original decision.
- [x] Calibration reports cannot change weights automatically.
- [ ] Production outcome-source adapters are selected and active.
- [ ] Real property outcomes have been collected and reviewed.

**State:** The local data model is ready; operational learning is not active.

## Owner Sign-Off

Owner: ____________________  Date: ____________________  Release: ____________________

Signing is intentionally blank. Neither Release A nor Release B has passed its complete gate.
