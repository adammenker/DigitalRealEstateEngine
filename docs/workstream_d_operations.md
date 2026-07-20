# Workstream D Operations

Workstream D makes asynchronous scans bounded and financially fail-closed. It does not enable
production traffic by itself, and verification makes no paid provider calls.

## Worker Runtime

FastAPI only initializes the database and serves requests. A separate process consumes queued
scans:

```bash
rank-rent worker --concurrency 2
```

Docker Compose runs this as the `worker` service. Each concurrency slot has its own worker ID and
claims a job with an atomic status compare-and-swap, unique lease token, and lease expiry.
Heartbeats extend the lease. SIGINT and SIGTERM stop new claims and allow active work to reach its
normal cancellation/checkpoint boundary.

Timeouts, rate limits, transient provider errors, and temporary database failures retry with
exponential backoff, bounded jitter, and `SCAN_WORKER_MAX_ATTEMPTS`. Authentication, invalid
plans, configuration/policy failures, schema mismatch, and invalid input do not retry. Exhausted
jobs become `quarantined` with their failed stage and reason. Stale leases use the same
retry/quarantine path.

Provider stages remain idempotent across attempts: completed responses replay from the durable
cache, completed planned requests cannot be consumed twice, and failed planned requests reuse
their existing call-ledger row.

## Paid-Call Policy

Every attached live cache miss reserves its planned request and estimated cost in the database
before the HTTP client opens. The reservation and final provider cost update durable daily summary
and endpoint buckets. Inspect counters and synthetic alert conditions with:

```bash
rank-rent data usage
```

Counters cover production/testing requests and spend, endpoint spend, cache misses, unexpected
calls, provider failures, and schema drift. Pull-based alerts cover unexpected calls, paid testing
responses, repeated cache misses, 50/80/100 percent spend thresholds, high provider/schema error
rates, paid calls without plan IDs, long-running scans, stale workers, poison jobs, and billing
mismatches. External pager/metrics delivery remains outside Workstream D.

All applicable switches must permit a call:

```text
ALLOW_LIVE_API_CALLS=true
ALLOW_PRODUCTION_DATAFORSEO=true   # production host only
PAID_CALL_KILL_SWITCH=false
ALLOW_FULL_SCANS=true              # full profile only
```

Durable limits and qualification/reconciliation state take effect on the next pre-call
transaction. Environment-variable switch changes require a process restart because settings are
cached per process.

Default limits are:

```text
PRODUCTION_DAILY_REQUEST_LIMIT=100
PRODUCTION_DAILY_SPEND_USD=25.00
TESTING_DAILY_SPEND_USD=2.00
SINGLE_CALL_ABNORMAL_COST_USD=1.00
```

Every call to the production provider host, including a testing-depth scan, uses production
counters and requires current qualification. `ALLOW_FULL_SCANS` remains specific to the full scan
profile. A new installation has one reconciliation grace window, controlled by
`BILLING_RECONCILIATION_MAX_AGE_HOURS`; after paid history ages beyond that window, production
calls fail closed until a recent clean billing reconciliation exists. A mismatch opens the circuit
immediately. Repeated unexpected calls and excessive provider/schema failure rates also open it.

## Qualification Matrix

The adapter version is `dataforseo-v3-workstream-d-2`. A qualification is current only when the
executable runner produced evidence for every required check, every check passed, the record has
not expired, its provider/environment match, and its adapter version is exact. Adapter changes
therefore invalidate previous results. The qualification runner may run before a gate-eligible
record exists, but it still honors credentials, production opt-in, the global paid-call switch,
and all daily request/spend limits. Ordinary market scans cannot use this exception.

Required JSON keys are:

```text
account_access, location_lookup, keyword_suggestions, keyword_metrics, serps,
serp_features, backlinks, business_listings, partial_tasks, rate_limits,
billing_errors, authentication_errors, schema_drift
```

Run the controlled qualification matrix:

```bash
rank-rent qualification run
rank-rent qualification status
```

The runner records per-check timestamps, sanitized evidence, failures, the executing operator, and
a SHA-256 evidence digest. Positive provider checks execute against the configured environment;
error handling and schema-drift checks execute deterministic adapter contract probes.

Historical or externally supplied results may be retained for audit:

```bash
rank-rent qualification record qualification-results.json \
  --reason "Imported historical provider certification"
```

Manual imports and overrides are always `gate_eligible=false`, even when every imported boolean is
true. They never unlock production paid calls. `rank-rent qualify --live` remains only an
account/location smoke check and is not complete production qualification.

## Lease Loss And Ambiguous Calls

Every asynchronous pipeline stage transition is conditioned on the current worker ID, lease token,
running status, and unexpired lease. The same lease is checked transactionally before cost
reservation and again before network submission. A failed heartbeat signals the active task to
stop; an old worker cannot complete a stage after another worker owns the scan.

API calls persist `prepared`, `reserved`, and `in_flight` attempt states. Recovery distinguishes:

- Attempts stopped before network submission become `failed_before_network`, release reserved
  spend, and may reuse the same planned request.
- Attempts that may have reached the provider become `provider_outcome_unknown`, move estimated
  cost into unreconciled spend, and permanently consume the planned request. They are never resent
  automatically.

Unknown outcomes open an operational alert and continue to count against the spend breaker until
an operator reconciles the call as billed or not billed with an auditable note.

```bash
rank-rent data resolve-unknown-call CALL_ID billed ACTUAL_COST \
  --reason "Matched provider billing export row 2026-07-20/abc123"
rank-rent data resolve-unknown-call CALL_ID not_billed 0 \
  --reason "Provider export and support case confirmed no request was accepted"
```

## Billing Reconciliation

Import a provider export with:

```bash
rank-rent billing reconcile dataforseo-billing.csv
```

Required columns are `provider_request_id`, `provider_task_id`, `endpoint`, `cost_usd`, and
`billed_at` (ISO-8601). Matching prefers request ID and falls back to task ID. The durable report
contains internal/provider call counts and costs, unmatched charges/calls, the difference, and a
`clean` or `mismatch` status. A mismatch or stale reconciliation opens the production-provider
circuit. The initial grace window avoids an impossible requirement to reconcile charges before
the first charge exists.

## Verification Boundary

Tests use fixture data, SQLite/PostgreSQL-compatible transactions, fake HTTP clients, and synthetic
incidents. They do not contact DataForSEO. Production still requires PostgreSQL deployment,
external alert delivery, credentials/secrets management, authentication, backup/restore rehearsal,
and release gates owned by other workstreams.
