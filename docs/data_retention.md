# Data Retention

## Lead records

`LeadRoutingPolicy.retention_days` assigns a precise `retention_expires_at` to
each lead. `LeadPrivacyService.enforce_retention()` anonymizes expired records
and returns the affected lead IDs. It is idempotent and leaves already deleted
records unchanged.

The default code value is 365 days. Production must configure and approve the
period for its jurisdiction and agreements instead of accepting the default
without review.

Consent text versions, non-PII delivery facts, and aggregate lifecycle events
remain after anonymization. This preserves evidence that the routing process
occurred without retaining the lead's contact data.

Anonymization also cancels pending, leased, delivering, and retrying jobs, clears
their worker leases, and prevents later retries from sending deleted lead data.

## Outcome records

Property outcomes are aggregate, source-attributed daily records.
`PropertyOutcomeService.enforce_retention()` removes records before an explicit
date and requires a privacy-admin access context. Calibration reports are
descriptive snapshots; deleting raw outcomes does not rewrite a previously
generated report.

The immutable property decision preserves the opportunity, original full
score, score version, evidence artifact, and selection date. It is not removed
by ordinary outcome retention because it is required to explain historical
property decisions.

## Operations

Retention jobs are not scheduled in this workstream. The production worker
must invoke both retention services under a privacy-admin service identity,
commit the transaction, emit counts without PII, and alert on failure.
