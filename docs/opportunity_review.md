# Opportunity Review and Approval

Workstream H turns discovery output into an attributable investment-review
workflow. It does not create properties, buy domains, generate sites, or route
leads.

## State model

The canonical opportunity states are:

```text
discovered
prefilter_review
testing_planned
testing_running
preliminary_review
full_scan_approved
full_running
full_review
needs_more_evidence
approved_for_property
rejected
archived
```

Transitions are validated by `OpportunityReviewService`. Every accepted
transition creates an `opportunity_reviews` row containing the prior and new
state, owner, reviewer, role, decision, reason, notes, tags, timestamps, and
monotonic review version. Clients may submit `expected_review_version` to reject
stale decisions.

Scanner lifecycle changes use the same service under the
`system:scan-pipeline` actor. System actors cannot approve or reject an
opportunity.

Approval can be reversed by moving an opportunity from
`approved_for_property` back to `full_review` or `needs_more_evidence`. Archival
is terminal.

## Actor contract

Review APIs derive their actor from the shared authenticated principal. Local and test
environments use the explicit local authentication adapter with `X-Local-User` and
`X-Local-Role`; staging and production require a validated OIDC bearer token. Supported review
roles are `operator`, `reviewer`, and `admin`. A client cannot claim the reserved `system` role.

The centralized mutation policy requires reviewer/admin authority for approval transitions and
evidence overrides. Accepted mutations append a hash-linked audit event in the same transaction.

## Approval completeness

`GET /api/opportunities/{id}/approval-completeness` checks:

- An assigned owner
- A completed full scan
- A full score tied to that scan
- Complete, rankable score evidence
- Passing or warning-only evidence quality
- Keyword, SERP, competitor, and provider records
- A reconciled API cost ledger
- Full evidence no older than the requested maximum age, 90 days by default

`approved_for_property` fails with `approval_evidence_incomplete` until every
required check passes. Missing-data flags and warning-level evidence remain
visible as underwriting warnings.

The existing property-decision and routing-profile entry points now call the
same approval guard. They cannot create downstream property state before the
opportunity is `approved_for_property`. Idempotent reads of a previously
created downstream record remain available if an approval is later withdrawn.

## Review APIs

```text
GET  /api/opportunities/{id}/review
POST /api/opportunities/{id}/review/owner
POST /api/opportunities/{id}/review/transition
GET  /api/opportunities/{id}/approval-completeness
```

Approval and rejection requests require a human actor and a decision reason.
The review response includes current ownership, version, allowed next states,
completeness, history, and overrides.

## Evidence overrides

Supported override types are:

- `serp_classification`
- `provider_suitability`
- `geographic_interpretation`
- `data_quality_warning`

The server reads and stores the original value. A request includes the new
value, reason, score impact, and score-impact explanation. Supplying
`expected_original_value` enables optimistic validation.

Overrides are overlays; original discovery records are not mutated. Reversal
appends a second row referencing the original override. This preserves the
entire history and makes active versus reverted interpretations explicit.

```text
GET  /api/opportunities/{id}/overrides
POST /api/opportunities/{id}/overrides
POST /api/opportunities/{id}/overrides/{override_id}/revert
```

## Saved discovery templates

Templates persist an owner, service family, market filters, prefilter profile,
testing/full profiles, budget, and freshness requirements.

```text
GET    /api/discovery-templates
POST   /api/discovery-templates
PUT    /api/discovery-templates/{id}
DELETE /api/discovery-templates/{id}
```

Deletion archives a template so historical batch plans remain attributable.
Template names are unique per owner.

## Cost-bounded batches

The batch flow is:

```text
create exact plans
-> verify aggregate estimate is within the declared budget
-> confirm an aggregate maximum and reason
-> queue the stored plans atomically
-> review each completed opportunity
```

Endpoints:

```text
POST /api/batch-scan-plans
GET  /api/batch-scan-plans/{id}
POST /api/batch-scan-plans/{id}/confirm
POST /api/batch-scan-plans/{id}/queue
```

The approved maximum cannot be lower than the estimate or higher than the
original budget. Queueing fails if any included opportunity already has an
active scan or if the stored aggregate would exceed the confirmed maximum.
Each queued scan references its batch, confirmer, and approved bound.

Testing batches move candidates to `testing_planned`. Full batches require
preliminary evidence and move each opportunity to `full_scan_approved` only
when the bounded batch is confirmed. A direct live full scan also requires
`full_scan_approved`; dry-run planning remains available before approval.

## Evidence packets

`GET /api/opportunities/{id}/evidence-packet` returns JSON by default. Add
`?format=csv` for a portable CSV envelope.

Packets include:

- Market and public-data evidence
- Keyword decisions
- SERP results and classifications
- Competitor evidence
- Provider evidence
- Score and component calculation trace
- Confidence
- Planned and actual API costs
- Evidence freshness
- Override history
- Review notes and approval completeness

CSV stores one record per section item with canonical JSON in `payload_json`,
preserving nested evidence without lossy column guessing.

## Migration

Migration `8b3e1f4a6c2d` extends `f8c1d4e7a2b9`. It adds review ownership and
version fields to opportunities and creates:

```text
opportunity_reviews
evidence_overrides
discovery_templates
batch_scan_plans
batch_scan_plan_items
```

Legacy statuses are normalized during upgrade:

```text
approved -> approved_for_property
evidence_rejected -> needs_more_evidence
scan_failed -> needs_more_evidence
partial_review -> needs_more_evidence
unusable_review -> needs_more_evidence
```

The current linear migration head is `d4a7c2e9f1b6`.
