# Production Backlog

Completed items describe behavior present in the repository. Open items remain required or
deliberately deferred before production use.

## Discovery Foundation

- [x] Use a versioned authoritative service catalog with stable IDs, aliases, seeds, intent
  modifiers, negative terms, and provider categories.
- [x] Allow explicit draft services for testing while rejecting drafts from full scans and
  promotion.
- [x] Use immutable per-scan `testing` and `full` profiles.
- [x] Promote eligible testing scans to full scans with lineage, an incremental request plan,
  and explicit nonzero-cost confirmation.
- [x] Keep preliminary assessments separate from full rankable assessments.
- [x] Restrict ranking and comparison to eligible full assessments.
- [x] Preserve latest assessment, typed score history, rescore reason, and score/component
  differences.
- [ ] Expand and review catalog coverage as additional service families enter the product.
- [ ] Calibrate score thresholds and weights against real ranking, lead, and revenue outcomes.

## Geography And Public Data

- [x] Scope initial discovery to U.S. populated places and Census ZCTAs.
- [x] Build a versioned offline index with canonical location IDs, city, state, ZIP, county,
  metro, coordinates, population, reference population, aliases, and provenance.
- [x] Require canonical resolution and a verified provider-search boundary before planning.
- [x] Require explicit selection for ambiguous fuzzy matches and reject unsupported markets.
- [x] Persist resolved geography and reuse the final resolved market during rescoring.
- [x] Add a versioned zero-cost public-data prefilter using ACS household, housing-unit,
  homeownership, and housing-age evidence.
- [ ] Add validated service-to-NAICS mappings before incorporating County Business Patterns
  or Nonemployer Statistics supply signals.
- [ ] Add service-specific climate or incidence signals only with documented causal models.
- [ ] Validate a richer local-demand estimator before increasing estimated-demand confidence
  or score influence.
- [ ] Add address-level or international resolution only when product scope requires it.

## Evidence Quality And Scoring

- [x] Gate evidence using keyword, representative-query, provider, geography, competitor, and
  SERP-classification coverage checks.
- [x] Mark failed evidence unusable, cap its score, and prevent it from ranking or promotion.
- [x] Label national, measured-local, and population-estimated demand separately.
- [x] Preserve per-group freshness and lower trust when evidence is stale or incomplete.
- [x] Use position- and query-aware SERP and competitor observations.
- [x] Distinguish page-scoped and domain-scoped competitor metrics and preserve unavailable
  metrics as null.
- [x] Provide component-specific calculations, missing-evidence effects, and confidence in
  discovery reports.
- [x] Test the complete discovery path with a realistic zero-network replay.
- [ ] Perform empirical evidence-gate and scoring calibration with production-quality samples.
- [ ] Establish labeled benchmark sets for SERP classification, provider suitability, and
  opportunity outcomes.

## DataForSEO Cost Controls

- [x] Default to DataForSEO sandbox and require explicit production opt-in.
- [x] Persist exact planned requests and require each attached live call to consume one unique
  unused plan entry before network access.
- [x] Reconcile planned and executed calls with cache, failure, unexpected-call, timing, and
  actual-cost details.
- [x] Show incremental uncached calls and estimated cost before full-scan promotion.
- [x] Enforce per-scan request and estimated-cost limits.
- [ ] Add durable per-day production spend limits.
- [ ] Alert on repeated cache misses, unexpected calls, paid testing responses, and abnormal
  provider cost.
- [ ] Validate provider-reported cost reconciliation against production billing exports.

## Operations

- [x] Run scans through a database-backed worker with atomic claim, heartbeat, cancellation,
  retry, stale recovery, and idempotent active retries.
- [x] Provide a confirmed one-command reset for local test data.
- [x] Select PostgreSQL for production, retain SQLite for local/replay use, and define explicit
  connection-pool and timeout policy with health/schema-readiness checks.
- [x] Store new live-provider raw responses in immutable filesystem or optional S3-compatible
  blobs with checksummed metadata and source-scan lineage.
- [ ] Run PostgreSQL concurrency tests and rehearse the documented encrypted database/blob
  backup and restore process in the selected production environment.
- [ ] Add production-grade structured logs, metrics, traces, and alerting by scan and call.
- [ ] Add deploy-time health checks for the frontend, backend, database, worker, and provider
  credentials.
- [x] Add CI gates for backend tests, frontend lint/build, migration checks, replay, and
  container builds.
- [ ] Operationalize the documented RPO/RTO, backup alerts, legal deletion, rollback,
  disaster-recovery, and data-retention procedures.

## Security And Secrets

- [ ] Move production credentials to a managed secret store and rotate existing shared test
  credentials before launch.
- [ ] Add authentication, authorization, and audit logging for production users and actions.
- [ ] Validate that logs, exports, errors, and API responses redact secrets and sensitive data.
- [ ] Add rate limits, request-size limits, dependency scanning, and production security
  headers.
- [ ] Document least-privilege access for DataForSEO and future external providers.

## Product Workflow

- [ ] Add saved discovery templates and batch prefilter-to-testing workflows.
- [ ] Add explicit opportunity review states, ownership, notes, and rejection reasons.
- [ ] Add evidence export and review workflows for competitors and provider candidates.
- [ ] Add approval gates before domain acquisition, site generation, outreach, or launch.
- [ ] Define how discovery outcomes feed site production, lead routing, tenant matching, and
  performance feedback.
