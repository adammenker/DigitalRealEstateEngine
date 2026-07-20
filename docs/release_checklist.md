# Release Checklist

Unchecked items are blockers, not optional polish.

## Gate A: Calibration Ready

- [ ] Benchmark harness and at least 20 scenarios pass.
- [ ] Pairwise business-direction invariants pass.
- [ ] Classification and provider benchmarks pass.
- [ ] Decision-affecting configuration is versioned and validated.

## Gate B: Paid Scan Ready

- [ ] DataForSEO production qualification is current.
- [ ] Every network call consumes one planned request.
- [ ] Per-scan and daily limits stop calls before network access.
- [ ] Kill switches and synthetic cost alerts pass.
- [ ] Provider billing reconciles with the internal ledger.

## Gate C: Internal Production Ready

- [ ] PostgreSQL concurrency tests pass.
- [ ] Blob persistence survives a database reset.
- [ ] Backup and restore rehearsal meets RPO/RTO targets.
- [ ] Authentication, RBAC, and audit logging are active.
- [ ] Staging logs, metrics, traces, and alerts are active.
- [ ] Deployment and rollback rehearsal passes.
- [ ] Opportunity review and approval workflows pass UAT.
- [ ] No unresolved critical security finding exists.

## Gate D: Property Staging Ready

- [ ] Approved opportunity is required.
- [ ] SiteConfig and provider assignment are versioned.
- [ ] Compliance review is approved.
- [ ] Staging is noindex and routing health passes.

## Gate E: Public Launch Ready

- [ ] Domain is operator-approved and verified.
- [ ] Referral disclosure and provider claims are approved.
- [ ] Forms, calls, analytics, privacy, and retention are verified.
- [ ] Production rollback is rehearsed.

## Gate F: Operational Learning Ready

- [ ] Search and lead outcomes link to original evidence and score version.
- [ ] Observed and provider-reported outcomes remain distinct.
- [ ] Historical rescoring preserves the original decision.
- [ ] Calibration reports run without automatic weight changes.

## Owner Sign-Off

Owner: ____________________  Date: ____________________  Release: ____________________

