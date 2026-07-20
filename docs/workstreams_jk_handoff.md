# Workstreams J/K Handoff

## Scope implemented

Internal lead intake, provider operations, fixture delivery/call routing,
analytics events, privacy lifecycle, immutable property outcome provenance,
fixture outcome ingestion, descriptive calibration reports, and manual scoring
change guardrails.

## Files changed

- `src/rank_rent/lead_routing/`: typed forms, adapters, ORM, routing,
  provider operations, analytics, redaction, access, export, and deletion.
- `src/rank_rent/outcomes/`: typed source adapters, immutable decisions,
  ingestion, retention/export/deletion, reports, and scoring guardrails.
- `migrations/versions/f8c1d4e7a2b9_...py`: J/K schema.
- `tests/unit/test_lead_routing.py` and `test_outcomes.py`: behavior coverage.
- `docs/lead_routing.md`, `provider_assignment.md`, `privacy.md`,
  `data_retention.md`, and `calibration.md`: implemented operations.
- `docs/adr/workstreams-jk-property-boundary.md`: cross-workstream boundary.

## Schema/config changes

Alembic revision `f8c1d4e7a2b9` creates routing profiles, provider assignments,
leads, lead events, consent/spam evidence, routing attempts, deliveries, lead
outcomes, analytics events, property decisions/outcomes, calibration reports,
and scoring change reviews. A partial unique index enforces one active provider
per property. No application configuration file was changed.

## Public interfaces changed

New Python protocols define email/phone delivery, call tracking, spam
assessment, rate limiting, operator alerts, and outcome sources. New service
interfaces cover provider lifecycle, lead submission, analytics ingestion,
privacy lifecycle, property decisions, outcome ingestion/reporting, and
review-only scoring proposals. Existing HTTP APIs are unchanged.

## Tests added

Coverage includes form/consent validation, stale consent rejection, spam,
rate limiting, idempotency, dedupe, durable retry/resume, alerts, PII-safe
logs, provider replacement, call health, analytics truth, lead outcomes,
access control, export/deletion/retention, outcome provenance/idempotency,
report truth separation/segments/correlations, and scoring-change guardrails.

## Commands run and results

```text
make verify
  Ruff: pass
  strict mypy: pass (67 source files)
  pytest: 188 passed, one third-party Starlette deprecation warning
  Next.js production build: pass
  Docker Compose image build: pass

make calibration: 26/26 scenarios, 12/12 pairwise expectations,
  12/12 SERP labels, 15/15 provider checks, zero network attempts
Alembic upgrade head -> downgrade b7d2f4a9c6e1 -> upgrade head: pass
git diff --check: pass
```

## Security implications

No real delivery, call, outcome-source network request, public endpoint, or
deployment is included. PII is redacted from persisted errors, adapter
metadata, and structured log contexts. Raw request metadata and contact
dedupe keys are HMACed. Access, export, anonymization, source deletion, and
retention are service-controlled. Recording defaults off.

## Operational implications

The lead service commits intake and each delivery attempt before awaiting an
adapter, allowing interrupted work to resume without duplicate deliveries.
Operator alerts receive identifiers and reason codes only. The in-memory rate
limiter and all delivery/outcome adapters are fixtures, not production
infrastructure.

## Known limitations

- Workstream I's property table is absent; J/K use an opaque property key.
- No authenticated HTTP form or provider portal is exposed.
- No shared rate limiter, scheduled retention job, or production alert sink is
  configured.
- Storage encryption, backup encryption, and secret management depend on the
  production data/security workstreams.
- Call recording remains blocked pending legal review and a secure adapter.

## Deviations

The workstream intentionally does not send test leads through real providers
or deploy a public endpoint, per the assignment. The property foreign key is
deferred rather than inventing Workstream I's schema; the opportunity, full
score, and evidence references are enforced now.

## Follow-up tasks

1. Repoint opaque `property_id` columns to Workstream I's authoritative
   property table during integration.
2. Rebase migration ancestry after parallel schema workstreams are merged.
3. Add authenticated internal endpoints only after security controls land.
4. Implement reviewed production adapters and shared rate limiting.
5. Schedule retention and call-route health jobs with operational alerts.
