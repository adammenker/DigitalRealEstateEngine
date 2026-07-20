# Lead Delivery Operations

Lead intake and provider delivery are separate transactions. Intake atomically stores the
validated lead, consent proof, spam assessment, assignment binding, lead event, and one durable
delivery job per configured channel. It returns `routing`; it never waits for an email, phone, or
call-routing provider.

## Worker

Local and staging fixture delivery runs in its own process:

```bash
rank-rent lead-worker --concurrency 1
```

The command uses non-network fixture adapters and refuses to start when `APP_ENV=production`.
Production must supply reviewed `DeliveryAdapter` and `OperatorAlertAdapter` implementations to
`run_lead_delivery_runtime()` before lead intake is enabled.

Each job has a unique delivery key, bounded attempt count, `next_attempt_at`, worker ID, lease
token, heartbeat, and lease expiry. Claims are conditional database updates, so only one worker
owns a delivery. The same delivery key is sent on every known-safe retry and is the provider
idempotency key.

## Recovery Rules

- A stale `leased` job is safe to requeue because no provider call began.
- A known transient adapter failure is retried with bounded exponential backoff.
- A permanent rejection or exhausted retry allowance ends the job as `failed`.
- A stale `delivering` job ends as `outcome_unknown` and is never automatically resent. An
  operator must reconcile it with the provider before any manual action.
- Deleting or anonymizing a lead cancels all nonterminal delivery jobs and clears their leases.
- Replaced, paused, or terminated assignments fail validation before provider contact. A queued
  job never silently switches to a different provider.

These rules favor avoiding duplicate provider contact over automatic recovery when the provider
outcome is ambiguous.

## Property Decision Lineage

Evidence artifacts carry a typed `scan_run_id`. A property decision stores that scan ID and uses
composite foreign keys to require its full score and evidence snapshot to come from the same scan.
The service also verifies opportunity, artifact type, and full-assessment status.

Property decisions are immutable after insertion. SQLAlchemy guards ORM updates and deletes;
SQLite and PostgreSQL migrations install database triggers that reject direct updates and deletes.
