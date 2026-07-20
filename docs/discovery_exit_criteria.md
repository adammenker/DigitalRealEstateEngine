# Discovery Exit Criteria

## Implemented Engineering Criteria

- [x] Fixture and replay scans complete without network access.
- [x] A realistic replay exercises keywords, SERPs, competitors, providers, scoring, reports,
  API ledger reconciliation, and rescore.
- [x] Live scans default to DataForSEO sandbox unless production is explicitly enabled.
- [x] Services resolve through a versioned catalog; drafts are testing-only.
- [x] Markets resolve through the offline U.S. geography index before planning.
- [x] Public-data prefiltering can rank markets without paid provider calls.
- [x] Testing and full profiles are selected and persisted per scan.
- [x] Testing scans can be promoted with lineage, plan review, and cost confirmation.
- [x] Every live market-scan call consumes a unique planned request before network access.
- [x] Reports reconcile planned and executed calls and expose actual provider cost.
- [x] Evidence quality, source mode, missing data, and freshness are visible.
- [x] Failed evidence is unusable and excluded from ranking, comparison, and promotion.
- [x] Only full assessments can update ranked opportunity scores.
- [x] Rescoring uses stored evidence and preserves reason, timestamp, and score differences.
- [x] Competitor records preserve query/position provenance and page/domain metric scope.

## Production Validation Criteria

- [ ] Score weights, thresholds, and evidence gates are calibrated against labeled real-world
  outcomes.
- [ ] Local-demand estimation is validated before receiving greater confidence or influence.
- [ ] Production database, backups, observability, alerts, and spend limits are exercised.
- [ ] Authentication, authorization, secret management, and audit logging are complete.
- [ ] CI and deployment gates run backend, frontend, migration, replay, and container checks.
- [ ] Product owners approve the review and handoff workflow from discovery to launch.

Discovery engineering is complete when `make verify` passes. Discovery is production-ready
only when the production validation criteria above are also satisfied.
