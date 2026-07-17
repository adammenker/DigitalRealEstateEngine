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

## Deferred

- The full scoring rewrite remains deferred; current scoring is still `v1` with existing directionality tests.
- Offline geographic resolution is not a complete U.S. city/ZIP/county dataset yet.
- Demand granularity is partially represented through metric granularity fields, but full national-versus-local estimation models are deferred.
- Qualification harness is still a smoke/fixture/replay foundation rather than a complete capability matrix.
- Async scans remain in-process background jobs; locking, cancellation, and durable worker restart semantics are not production-grade.
- Site generator hardening is deferred beyond preserving the existing workflow separation.

## Rationale

The repository already had several offline remediation pieces before this spec arrived. This pass focuses on the first task named by the spec plus the highest-risk schema, cache, replay, and scan metadata gaps without consuming paid API credits.
