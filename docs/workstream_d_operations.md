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
rates, stale workers, poison jobs, and billing mismatches. External pager/metrics delivery remains
outside Workstream D.

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

Production full calls also fail closed without current qualification and recent clean billing
reconciliation, after repeated unexpected calls, or when provider/schema failure rates exceed
configured thresholds.

## Qualification Matrix

The adapter version is `dataforseo-v3-workstream-d-1`. A qualification is current only when every
required check passes, it has not expired, its provider/environment match, and its adapter version
is exact. Adapter changes therefore invalidate previous results.

Required JSON keys are:

```text
account_access, location_lookup, keyword_suggestions, keyword_metrics, serps,
serp_features, backlinks, business_listings, partial_tasks, rate_limits,
billing_errors, authentication_errors, schema_drift
```

Record results from a controlled qualification run without making another call:

```bash
rank-rent qualification record qualification-results.json
rank-rent qualification status
```

`rank-rent qualify --live` is only an account/location smoke check and is explicitly not complete
production qualification.

## Billing Reconciliation

Import a provider export with:

```bash
rank-rent billing reconcile dataforseo-billing.csv
```

Required columns are `provider_request_id`, `provider_task_id`, `endpoint`, `cost_usd`, and
`billed_at` (ISO-8601). Matching prefers request ID and falls back to task ID. The durable report
contains internal/provider call counts and costs, unmatched charges/calls, the difference, and a
`clean` or `mismatch` status. A mismatch or stale reconciliation opens the production-full circuit.

## Verification Boundary

Tests use fixture data, SQLite/PostgreSQL-compatible transactions, fake HTTP clients, and synthetic
incidents. They do not contact DataForSEO. Production still requires PostgreSQL deployment,
external alert delivery, credentials/secrets management, authentication, backup/restore rehearsal,
and release gates owned by other workstreams.
