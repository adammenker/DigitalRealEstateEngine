# V1 Hardening Deviations

This file records intentional deviations from `specs/DigitalRealEstateEngine_V1_HARDENING_SPEC.md`.

## Implemented In This Slice

- Alembic migrations are restored and extended for V1 metadata.
- `ScanRun` stores mode, profile, adapter names/versions, normalization version, scoring version, cache policy version, planned cost, source scan, progress stage, retry count, cancellation flag, and partial outputs.
- Stored DataForSEO responses now include response shape version, sanitized flag, provider request ID, source scan ID, checksum, and expiry.
- Live-mode DataForSEO requests target the free sandbox host by default and only use production when `DATAFORSEO_ENVIRONMENT=production`.
- Replay bundles are checksum validated and rejected when corrupted or unsanitized.
- Cache keys include provider, endpoint, API version, response shape version, and normalized request.
- Scan plans expose cache hit count, paid call count, request limit, confirmation requirement, and cost budget blocks.
- Preliminary/full assessment and score-component tables are added.
- Async scans now use a database-backed in-process worker with atomic claim, heartbeat,
  stale recovery, cancellation, and idempotent active retry behavior.
- Keyword handling now records exact duplicates, negative filters, close-variant clusters,
  representative SERP selections, and value-ranking reasons.

## Deferred

- The full scoring rewrite is no longer deferred. Discovery completion implements scoring `v2`
  with demand evidence, commercial value, competitor weakness, organic click availability,
  provider suitability, and data completeness.
- Offline geographic resolution is not a complete U.S. city/ZIP/county dataset yet.
- Demand granularity is represented in scoring/reporting. Local demand estimation is deliberately
  conservative and only uses transparent provider-local volume or population-share estimation
  when population metadata exists.
- Qualification harness is still a smoke/fixture/replay foundation rather than a complete capability matrix.
- A separate external queue service is deferred; V1 intentionally uses the backend process worker.
- Site generator hardening is deferred beyond preserving the existing workflow separation.

## Rationale

The repository already had several offline remediation pieces before this spec arrived. This pass focuses on the first task named by the spec plus the highest-risk schema, cache, replay, and scan metadata gaps without consuming paid API credits.
