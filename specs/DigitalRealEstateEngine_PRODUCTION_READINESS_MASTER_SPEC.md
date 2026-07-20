# DigitalRealEstateEngine — Production Readiness Master Specification

**Repository:** `adammenker/DigitalRealEstateEngine`  
**Baseline:** Current `main` reviewed July 2026  
**Audience:** Codex orchestrator and Codex subagents  
**Purpose:** Complete the remaining engineering, operational, security, calibration, and product work required to move the current discovery beta into a production-ready rank-and-rent operating platform.

---

# 1. Executive objective

The repository already contains a substantially complete discovery workflow:

```text
configured service
→ canonical U.S. market
→ zero-cost public-data prefilter
→ testing scan
→ evidence-quality gate
→ promotion
→ full scan
→ scoring
→ comparison
→ replay and rescoring
```

This specification covers the remaining work needed to make that system safe and dependable in production and then connect approved opportunities to controlled property launch and performance feedback workflows.

Production readiness does **not** mean claiming that a score guarantees rankings, leads, revenue, or provider adoption. It means:

1. The system can safely spend real API money.
2. Discovery results are calibrated, versioned, explainable, and reviewable.
3. Production data is durable, recoverable, and auditable.
4. Operators can authenticate and perform controlled workflows.
5. Scans are observable, bounded, retryable, and reversible.
6. Approved opportunities can move through gated property-launch workflows.
7. Lead and search outcomes can be measured and fed back into future calibration.
8. Failures do not silently corrupt data, overspend, expose secrets, or launch misleading sites.

---

# 2. Release targets

The work must be delivered through two explicit release targets.

## Release A — Production discovery platform

Release A is complete when the system can be deployed privately and used by the owner to:

- Generate and rank market candidates.
- Run bounded production DataForSEO scans.
- Review, compare, annotate, approve, and reject opportunities.
- Preserve evidence, cost, lineage, and score history.
- Recover from failures.
- Operate securely with production monitoring and backups.

Release A does **not** require publicly launched rank-and-rent websites.

## Release B — Production property operations

Release B is complete when an approved opportunity can move through:

```text
approved opportunity
→ property configuration
→ domain selection
→ exclusive provider configuration
→ staging site
→ compliance review
→ production deployment
→ call/form routing
→ analytics
→ provider performance review
→ provider replacement when necessary
```

Release B must remain blocked until Release A passes its production-readiness gate.

---

# 3. Non-negotiable business rules

1. Do not create fake Google Business Profiles.
2. Do not create fake addresses, reviews, employees, credentials, licenses, or business histories.
3. A public property must clearly disclose its referral relationship.
4. The property must remain owned independently from the active provider.
5. Only one active provider should be presented for an exclusive-rental property.
6. Provider replacement must be a configuration operation, not a rewrite of the entire SEO asset.
7. Do not send outreach automatically without an explicit operator action.
8. Do not purchase domains automatically without an explicit operator action.
9. Do not deploy publicly without an approval gate.
10. Do not let preliminary assessments participate in full opportunity rankings.
11. Do not run paid production scans when evidence qualification or budget controls fail.
12. Preserve all purchased raw provider responses and their lineage.
13. Scores are decision-support signals, not guarantees.
14. Unknown evidence must remain unknown; never silently replace it with zero or fabricated values.
15. Public-data prefiltering must remain separate from SEO-opportunity scoring.

---

# 4. Codex orchestrator rules

The lead Codex agent must coordinate all subagents.

## 4.1 Required orchestration files

Create and maintain:

```text
docs/production_status.md
docs/production_handoff.md
docs/production_deviations.md
docs/production_architecture.md
docs/production_runbook.md
docs/release_checklist.md
```

Also create a machine-readable status file:

```text
docs/production_status.yaml
```

Suggested structure:

```yaml
baseline_commit: ""
release_target: "A"
workstreams:
  calibration:
    status: not_started
    owner: ""
    branch: ""
    blockers: []
    completed_acceptance_criteria: []
  production_data:
    status: not_started
    owner: ""
    branch: ""
    blockers: []
    completed_acceptance_criteria: []
```

## 4.2 Branching and integration

Each subagent must:

1. Work on a dedicated branch.
2. Rebase on the integration branch before final handoff.
3. Run all relevant local checks.
4. Write a structured handoff.
5. Avoid editing unrelated workstream files unless necessary.
6. Record cross-workstream schema or interface changes in an ADR.
7. Never merge its own branch unless acting as the orchestrator.
8. Never resolve a test failure by weakening the intended invariant.

## 4.3 Required subagent handoff

Every subagent must report:

```text
Scope implemented
Files changed
Schema/config changes
Public interfaces changed
Tests added
Commands run
Results
Security implications
Operational implications
Known limitations
Deviations
Follow-up tasks
```

## 4.4 Verification

The orchestrator must run:

```bash
make verify
```

after every integration and before every release gate.

Production code must never require live paid API access for CI.

---

# 5. Suggested subagent allocation

| Subagent | Workstream | Primary dependency |
|---|---|---|
| A | Calibration and benchmark harness | Existing discovery pipeline |
| B | Service-specific public-data prefilter | Geography and service catalog |
| C | Production database and durable storage | Stable discovery schema |
| D | Worker reliability and API spending controls | Production database |
| E | Authentication, authorization, audit, and secrets | Production deployment design |
| F | Observability, SLOs, alerts, and runbooks | Worker and API-call ledger |
| G | Deployment, environments, CI/CD, rollback | Database, security, observability |
| H | Opportunity review and approval workflow | Release A discovery model |
| I | Property, domain, and site-production workflow | Opportunity approval |
| J | Lead routing, analytics, provider operations | Property deployment |
| K | Outcome feedback and calibration reporting | Live property outcomes |
| L | Independent QA and release validation | All workstreams |

Subagents may work in parallel only when their dependencies are stable.

---

# 6. Dependency graph

```text
Calibration harness ───────────────┐
Service-specific prefilter ────────┤
                                   ├→ Release A discovery gate
Production DB → Worker controls ───┤
Security → Deployment ─────────────┤
Observability ─────────────────────┘

Release A discovery gate
        ↓
Opportunity review and approvals
        ↓
Property/domain/site workflow
        ↓
Lead routing and analytics
        ↓
Outcome feedback
        ↓
Release B gate
```

---

# 7. Phase 0 — Production baseline and architecture freeze

## Objective

Create an accurate baseline before subagents modify production-critical behavior.

## Tasks

1. Record:
   - Current commit SHA
   - Python version
   - Node version
   - Docker version
   - Dependency lock status
   - Current migrations
   - Current scoring version
   - Current evidence-quality version
   - Current service-catalog version
   - Current geography dataset version
2. Generate an architecture diagram covering:
   - Frontend
   - Backend
   - Worker
   - Database
   - Raw-response storage
   - DataForSEO
   - Public-data ingestion
   - Future site deployment
3. Inventory all:
   - Environment variables
   - External providers
   - Network calls
   - Secrets
   - Files containing user or provider data
   - Background tasks
4. Run:
   - Ruff
   - Strict mypy
   - Full pytest
   - Frontend lint/build
   - Migration test
   - Docker build
   - Fixture E2E
   - Replay E2E
5. Add architectural decision records:

```text
docs/adr/0001-production-database.md
docs/adr/0002-job-execution.md
docs/adr/0003-raw-response-storage.md
docs/adr/0004-authentication.md
docs/adr/0005-deployment-platform.md
docs/adr/0006-observability.md
```

## Acceptance criteria

- All current checks and failures are documented.
- Production interfaces are identified before implementation.
- Every network-capable code path is inventoried.
- No production implementation begins without the relevant ADR.

---

# 8. Workstream A — Calibration and benchmark harness

## Objective

Prove that the scoring and evidence gates behave sensibly before broad production use.

## 8.1 Benchmark scenario format

Create a versioned format:

```yaml
scenario_id: strong_demand_weak_competition
version: 1
description: ""
service_family_id: water_heater_services
market:
  canonical_id: ""
data_mode: benchmark

evidence:
  keyword_metrics: []
  serp_snapshots: []
  competitors: []
  providers: []
  demand: {}
  freshness: {}

expectations:
  rankable: true
  assessment_type: full
  confidence_in: [medium, high]
  component_ranges:
    competitor_weakness: [15, 25]
  invariants: []
```

Benchmark scenarios must use the same normalization and scoring paths as replay data.

## 8.2 Required scenario library

Add at least 20 scenarios covering:

1. Strong demand, weak competition, healthy providers
2. Strong demand, strong competition
3. Weak demand, weak competition
4. Weak demand, strong competition
5. Directory-dominated top results
6. Marketplace-dominated top results
7. National-brand-dominated results
8. Strong local providers in top positions
9. Multiple weak generic pages
10. Too few providers
11. Provider oversupply
12. Providers mostly irrelevant
13. Providers outside the geographic boundary
14. Mostly unknown SERP classifications
15. National demand only
16. Measured local demand
17. Population-estimated local demand
18. Stale SERPs
19. Missing backlink evidence
20. Partial provider evidence
21. Failed evidence gate
22. Testing/preliminary assessment
23. Same competitor across several valuable queries
24. Strong competitor at position one versus position ten

## 8.3 Pairwise expectations

Create pairwise assertions:

```yaml
pairwise_expectations:
  - preferred: strong_demand_weak_competition
    over: strong_demand_strong_competition
    reason: weaker competition
```

Tests should assert relative ordering rather than arbitrary exact scores where possible.

## 8.4 Classification benchmark

Create labeled fixture sets for:

- Local provider
- Directory
- Marketplace
- Lead generator
- National brand
- Informational publisher
- Government/nonprofit
- Unknown

Include ambiguous cases and confidence expectations.

## 8.5 Provider benchmark

Create labeled providers covering:

- Exact service-category fit
- Adjacent service only
- Confirmed active
- Unknown status
- Closed
- In market
- Outside market
- Contactable
- Non-contactable
- Strong review evidence
- Sparse review evidence

## 8.6 Calibration CLI

Add:

```bash
rank-rent calibrate run
rank-rent calibrate report
rank-rent calibrate compare <scoring-version-a> <scoring-version-b>
rank-rent calibrate validate-config
```

Output:

- Scenario results
- Failed pairwise expectations
- Component distributions
- Evidence-gate confusion summary
- Classification accuracy summary
- Provider benchmark summary
- Score-version diff
- Configuration hash

## 8.7 Calibration discipline

Rules:

- Adjust configuration rather than adding scenario-specific exceptions.
- Every scoring change increments the version.
- Preserve historical benchmark output.
- Record rationale for weight/threshold changes.
- Do not use real business outcomes until they exist.
- Synthetic scenarios validate direction, not profitability.

## Acceptance criteria

- At least 20 benchmark scenarios pass.
- All business-direction invariants pass.
- Classification and provider benchmark reports are generated.
- A scoring-version comparison requires zero network calls.
- No production DataForSEO calls occur.
- The benchmark suite runs in CI.

---

# 9. Workstream B — Public-data prefilter V2

## Objective

Use free or inexpensive public data to reduce DataForSEO calls without conflating market plausibility with SEO difficulty.

## 9.1 Naming and separation

Rename the public-data result to:

```text
AddressableMarketAssessment
```

Avoid names implying that it measures complete SEO opportunity.

The result must remain outside the final SEO score.

## 9.2 Service-specific profiles

Add versioned profiles for each configured service family.

Example:

```yaml
service_family_id: roofing
profile_version: 1

signals:
  households:
    weight: 0.20
  owner_occupied_units:
    weight: 0.20
  housing_age:
    weight: 0.20
  detached_housing_share:
    weight: 0.10
  storm_exposure:
    weight: 0.20
  purchasing_power:
    weight: 0.10
```

Every signal must document:

- Source
- Date/version
- Causal rationale
- Expected direction
- Missing-data treatment
- Geographic granularity
- Refresh cadence

## 9.3 Public datasets

Implement adapters and durable cached ingestion for:

- ACS household and housing measures
- County Business Patterns after validated NAICS mapping
- Nonemployer Statistics after validated NAICS mapping
- Optional NOAA climate measures
- Optional FEMA hazard measures

Do not add a dataset merely because it is available.

## 9.4 NAICS mapping registry

Create a reviewed registry:

```yaml
service_family_id: water_heater_services
naics:
  - code: "238220"
    relationship: broad_parent
    confidence: medium
    notes: ""
```

Rules:

- Distinguish exact, broad-parent, and adjacent mappings.
- Broad mappings receive lower confidence.
- Never present broad NAICS counts as exact provider counts.
- Store dataset year and release date.

## 9.5 Provider-density signals

Calculate:

```text
employer establishments per 10,000 target households
nonemployer businesses per 10,000 target households
combined supply band
data confidence
```

Use configurable ideal ranges rather than assuming lower is always better.

## 9.6 Batch candidate workflow

Add:

```bash
rank-rent prefilter batch --service <id> --markets <file>
rank-rent prefilter top --service <id> --limit 100
```

Frontend workflow:

```text
Choose service
→ select state/metro/market set
→ run free prefilter
→ inspect evidence
→ select candidates
→ create testing scan plans
```

No DataForSEO call should happen at the prefilter stage.

## 9.7 Data refresh

Implement:

- Dataset version registry
- Download checksum
- Source provenance
- Staging import
- Validation
- Atomic activation
- Rollback to previous dataset
- Refresh CLI
- Data-age warnings

## Acceptance criteria

- Profiles differ by service family.
- Prefilter scores display exact evidence and limitations.
- NAICS signals are confidence-labeled.
- Batch prefiltering makes zero DataForSEO calls.
- Public-data refreshes are reproducible and reversible.
- Prefilter remains separate from the SEO score.
- Missing public data does not fabricate a score.

---

# 10. Workstream C — Production database and durable storage

## Objective

Replace local-only persistence with production-grade durable data while respecting the project's early-stage migration preference.

## 10.1 Database selection

Use PostgreSQL for production.

SQLite remains supported for:

- Local development
- Fixture tests
- Lightweight replay tests

## 10.2 Clean cutover strategy

The project is pre-production. Do not spend time preserving every prototype database version.

Use this strategy:

1. Declare a production schema cutover commit.
2. Wipe disposable local prototype databases.
3. Create one clean production baseline migration.
4. Preserve raw API response/replay bundles separately.
5. Require forward migrations from the cutover onward.
6. Test future upgrades against populated production-like fixtures.

## 10.3 Connection management

Implement:

- Environment-specific connection URLs
- Explicit pool sizing
- Pool timeout
- Statement timeout
- Transaction timeout
- Health check
- Readiness check
- Worker-safe sessions
- Web-request-safe sessions

## 10.4 Concurrency and locking

Validate:

- Atomic job claim
- Heartbeat
- Cancellation
- Retry
- Stale recovery
- Idempotency
- Planned-call reservation
- Concurrent rescoring
- Concurrent opportunity review

Use PostgreSQL row-level locks where appropriate.

## 10.5 Raw response storage

Move large immutable raw responses and replay bundles to object storage or a filesystem abstraction.

Required interface:

```python
class BlobStore(Protocol):
    def put(...)
    def get(...)
    def exists(...)
    def delete(...)
    def checksum(...)
```

Production adapter may use S3-compatible storage.

Database records store:

- Object key
- Checksum
- Content type
- Size
- Provider
- Endpoint
- Source scan
- Retention classification
- Encryption status
- Created timestamp

## 10.6 Backup and restore

Implement and document:

- Automated PostgreSQL backups
- Point-in-time recovery where available
- Object-store versioning
- Backup encryption
- Restore test
- Recovery-time objective
- Recovery-point objective

Minimum initial targets:

```text
RPO: 24 hours
RTO: 4 hours
```

Tighten later if necessary.

## 10.7 Data retention

Define retention for:

- Raw provider responses
- API-call ledger
- Scan plans
- Scores
- Audit events
- Provider contact data
- Lead data
- Logs
- Deleted opportunities

Support legal/manual deletion workflows.

## Acceptance criteria

- Production runs on PostgreSQL.
- Local fixture mode still supports SQLite.
- A clean baseline migration exists.
- Forward migrations are tested.
- Concurrent-worker integration tests pass on PostgreSQL.
- Backup and restore are successfully rehearsed.
- Raw paid data survives a database reset.
- Retention policies are documented and enforced.

---

# 11. Workstream D — Worker reliability and production cost controls

## Objective

Guarantee that production scans are bounded, observable, idempotent, and financially safe.

## 11.1 Worker process

Run the worker as a separate production process from the API server.

Support:

- Configurable concurrency
- Graceful shutdown
- Lease-based job ownership
- Heartbeat
- Cancellation
- Retry policy
- Stale-job recovery
- Poison-job quarantine
- Idempotent stage execution

## 11.2 Retry policy

Classify errors:

```text
retryable:
  timeout
  transient provider error
  rate limit
  temporary database failure

non_retryable:
  invalid request
  failed evidence prerequisites
  authentication failure
  budget exceeded
  schema mismatch
```

Use exponential backoff with jitter and a maximum attempt count.

## 11.3 Daily spending controls

Add durable counters for:

```text
production requests today
production spend today
testing requests today
testing spend today
provider endpoint spend
cache misses
unexpected calls
```

Configuration:

```yaml
production_limits:
  daily_request_limit: 100
  daily_spend_usd: 25.00
  testing_daily_spend_usd: 2.00
  single_call_abnormal_cost_usd: 1.00
```

Values are environment-specific.

## 11.4 Circuit breakers

Block new paid calls when:

- Daily request limit reached
- Daily spend limit reached
- Provider billing cannot be reconciled
- Repeated unexpected calls occur
- Credential qualification fails
- Schema-drift rate exceeds threshold
- Provider failure rate exceeds threshold
- Operator activates global kill switch

## 11.5 Kill switches

Add:

```text
ALLOW_LIVE_API_CALLS
ALLOW_PRODUCTION_DATAFORSEO
PAID_CALL_KILL_SWITCH
ALLOW_FULL_SCANS
```

Production paid calls require all applicable controls.

## 11.6 Alert conditions

Alert on:

- Unexpected paid call
- Paid call with no planned request ID
- Paid testing response
- Repeated cache misses
- Abnormal endpoint cost
- Daily spend at 50%, 80%, and 100%
- Provider-reported versus internal cost mismatch
- High error rate
- Long-running scan
- Stale worker
- Poison job

## 11.7 Billing reconciliation

Implement import and comparison against provider billing exports.

Report:

```text
internal call count
provider call count
internal cost
provider cost
unmatched provider charges
unmatched internal calls
difference
```

## 11.8 Qualification matrix

Before enabling production scanning, validate:

- Account access
- Location lookup
- Keyword suggestions
- Keyword metrics
- SERPs
- SERP features
- Backlinks
- Business listings
- Partial tasks
- Rate limits
- Billing errors
- Authentication errors
- Schema drift

Qualification results must expire and be rerun after adapter-version changes.

## Acceptance criteria

- Worker runs separately from API.
- Paid calls stop at daily limits.
- Unexpected calls fail before network access.
- Retry policy is tested.
- Kill switch is immediate.
- Billing reconciliation produces a clean report.
- Production scanning is blocked without current qualification.
- Alerts are tested through synthetic incidents.

---

# 12. Workstream E — Authentication, authorization, audit, and secrets

## Objective

Prevent unauthorized access and make all production actions attributable.

## 12.1 Authentication

Initial production may be single-operator, but authentication is still required.

Use a standards-based approach:

- OIDC/OAuth through a managed identity provider, or
- Secure passwordless login

Do not implement homemade password storage unless unavoidable.

## 12.2 Roles

Initial roles:

```text
admin
operator
reviewer
read_only
```

Permissions:

| Action | Admin | Operator | Reviewer | Read only |
|---|---:|---:|---:|---:|
| View opportunities | ✓ | ✓ | ✓ | ✓ |
| Run testing scan | ✓ | ✓ |  |  |
| Run full scan | ✓ | ✓ |  |  |
| Override evidence | ✓ |  | ✓ |  |
| Approve opportunity | ✓ |  | ✓ |  |
| Change production limits | ✓ |  |  |  |
| Deploy property | ✓ | ✓ |  |  |
| View secrets |  |  |  |  |

Do not expose secret values through the UI.

## 12.3 Audit logging

Audit:

- Login/logout
- Scan creation
- Cost confirmation
- Full-scan promotion
- Cancellation/retry
- Evidence override
- Rescore
- Opportunity approval/rejection
- Domain selection
- Provider activation
- Site deployment
- Routing changes
- Data export
- Data deletion
- Configuration changes

Audit records must be append-only.

## 12.4 Secret management

Production secrets must use a managed secret store.

Requirements:

- No secrets in repository
- No secrets in container images
- No secrets in logs
- Rotation runbook
- Environment separation
- Least-privilege provider credentials
- Startup validation
- Secret-reference support

Rotate any shared test credentials before production.

## 12.5 Web security

Implement:

- Secure cookies
- CSRF protection where relevant
- Strict CORS
- Content Security Policy
- HSTS
- X-Content-Type-Options
- Referrer Policy
- Frame restrictions
- Request-size limits
- Rate limiting
- Input validation
- Safe error messages
- Upload restrictions
- SSRF protections for URL-fetching adapters

## 12.6 Dependency security

Add CI checks for:

- Python dependency vulnerabilities
- NPM dependency vulnerabilities
- Secret scanning
- Static analysis
- Container image vulnerabilities
- License policy

## Acceptance criteria

- All production routes require authentication except health endpoints.
- Unauthorized users cannot trigger paid scans.
- Privileged actions require correct roles.
- Audit events include actor and target.
- Secrets are managed outside the repository.
- Security headers and rate limits are tested.
- CI blocks critical dependency vulnerabilities or requires documented exception.

---

# 13. Workstream F — Observability, SLOs, and incident response

## Objective

Make production behavior understandable without opening the database manually.

## 13.1 Structured logging

Use structured JSON logs with:

```text
timestamp
level
environment
service
version
request_id
scan_run_id
opportunity_id
planned_request_id
provider
endpoint
user_id
event
duration_ms
cost_usd
error_type
```

Never log credentials or full sensitive payloads.

## 13.2 Metrics

Expose metrics for:

### API

- Request count
- Error count
- Latency
- Authentication failures
- Rate-limit responses

### Worker

- Queue depth
- Oldest queued job
- Active jobs
- Stage duration
- Retry count
- Stale jobs
- Failed jobs
- Cancelled jobs

### Provider

- Calls by endpoint
- Cache hit rate
- Cost
- Error rate
- Rate limits
- Schema mismatch
- Response latency

### Discovery

- Testing scans
- Full scans
- Evidence-gate pass rate
- Rankable opportunities
- Confidence distribution
- Rescores
- Score-version distribution

### Property operations

- Deployed properties
- Form submissions
- Calls
- Routing failures
- Provider response outcomes

## 13.3 Tracing

Add distributed traces across:

```text
frontend request
→ API
→ scan creation
→ worker stage
→ provider call
→ persistence
```

Include scan and planned-call IDs.

## 13.4 SLOs

Initial SLOs:

```text
API availability:                 99.5%
Successful queued scan pickup:    99%
No duplicate paid calls:          100%
No unplanned paid calls:          100%
Cost-ledger reconciliation:       100%
Data-loss incidents:              0
```

Define service-level indicators and error budgets.

## 13.5 Alerts

Add alerts for:

- API unavailable
- Database unavailable
- Worker unavailable
- Queue age above threshold
- Repeated scan failures
- Cost limit exceeded
- Unexpected paid call
- Backup failure
- Restore check failure
- Authentication anomaly
- Deployment health failure
- Lead-routing failure

## 13.6 Incident runbooks

Create runbooks for:

```text
DataForSEO overspend
Provider outage
Database outage
Worker stuck
Bad scoring release
Corrupt geography/public dataset
Credential leak
Bad public deployment
Lead-routing outage
Lost provider routing
```

## Acceptance criteria

- Logs correlate one scan across all stages.
- Metrics and traces are available in staging.
- Alerts are exercised through test incidents.
- Every P0/P1 incident type has a runbook.
- Dashboards show cost, queue, error, and discovery health.

---

# 14. Workstream G — Environments, deployment, CI/CD, and rollback

## Objective

Produce reproducible local, staging, and production environments.

## 14.1 Environment model

Support:

```text
local
test
staging
production
```

Each environment must have separate:

- Database
- Secrets
- Object storage
- Provider credentials
- Cost limits
- Domains
- Logs
- Metrics
- Authentication configuration

## 14.2 Infrastructure as code

Define production infrastructure using one framework:

- Terraform, or
- AWS CDK if AWS is selected

Do not manage production infrastructure solely through manual console changes.

## 14.3 Container images

Requirements:

- Multi-stage builds
- Non-root user
- Minimal base image
- Pinned runtime versions
- Health checks
- Immutable image tags
- SBOM
- Vulnerability scan
- No secrets
- Reproducible build

## 14.4 Deployment sequence

Production deployment:

```text
build
→ test
→ security scan
→ publish immutable images
→ backup verification
→ run migration job
→ deploy backend
→ deploy worker
→ deploy frontend
→ run smoke tests
→ enable traffic
```

## 14.5 Health endpoints

Provide:

```text
/live
/ready
/health/dependencies
```

Readiness must validate required production dependencies without issuing paid provider calls.

## 14.6 Rollback

Support:

- Application rollback
- Worker rollback
- Frontend rollback
- Configuration rollback
- Dataset rollback
- Scoring-version rollback
- Database migration rollback where safe
- Forward-fix procedure where downgrade is unsafe

## 14.7 Release artifacts

Every release records:

```text
Git SHA
image digests
migration version
scoring version
evidence-quality version
service-catalog version
geography version
prefilter version
release notes
```

## 14.8 CI/CD

CI must include:

- Ruff
- Strict mypy
- Pytest
- Calibration benchmarks
- Fixture E2E
- Replay E2E
- PostgreSQL integration tests
- Migration tests
- Frontend lint/build
- Security scans
- Container builds
- Site-generator tests
- Contract tests

Deployment requires manual approval for production.

## Acceptance criteria

- Staging and production are isolated.
- A clean staging deployment is automated.
- Production deployment requires approval.
- Rollback is rehearsed.
- Release metadata is queryable.
- No paid calls occur during deployment health checks.
- Production secrets never enter CI logs.

---

# 15. Workstream H — Opportunity review and approval workflow

## Objective

Turn raw discovery results into a controlled investment-review process.

## 15.1 Opportunity states

Add:

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

Use validated transitions.

## 15.2 Review record

Persist:

```text
opportunity_id
review_state
owner_user_id
reviewer_user_id
decision
decision_reason
notes
tags
created_at
updated_at
approved_at
rejected_at
```

## 15.3 Evidence overrides

Allow explicit overrides for:

- SERP classification
- Provider suitability
- Geographic interpretation
- Data-quality warning

Every override requires:

- Actor
- Original value
- New value
- Reason
- Timestamp
- Score impact
- Reversible history

## 15.4 Saved templates

Support saved discovery templates:

```text
service family
market filters
prefilter profile
testing profile
full profile
budget
freshness requirements
```

## 15.5 Batch workflow

Allow:

```text
run addressable-market prefilter
→ select candidates
→ generate testing plans
→ confirm aggregate cost
→ queue testing scans
→ review results
```

Full scans should still require explicit per-opportunity or bounded batch approval.

## 15.6 Evidence export

Export review packets containing:

- Market evidence
- Keyword decisions
- SERPs
- Competitors
- Providers
- Score trace
- Confidence
- Costs
- Freshness
- Overrides
- Review notes

Formats:

```text
JSON
CSV
human-readable HTML or PDF later
```

## Acceptance criteria

- Invalid state transitions fail.
- Every approval/rejection is attributable.
- Evidence overrides are auditable and reversible.
- Approved opportunities contain all required evidence.
- Batch testing respects aggregate cost limits.
- No property action is available before `approved_for_property`.

---

# 16. Release A production gate

Release A may be approved only when:

1. Calibration benchmarks pass.
2. Production DataForSEO qualification passes.
3. No unplanned paid call can reach the network.
4. Daily budget limits and kill switches work.
5. PostgreSQL concurrency tests pass.
6. Backups and restore are tested.
7. Authentication and authorization are active.
8. Audit logs cover privileged actions.
9. Staging observability and alerts are active.
10. Deployment and rollback are rehearsed.
11. Opportunity review states are complete.
12. The service catalog and public-data profiles in use are reviewed.
13. Security scans have no unresolved critical findings.
14. Operator runbooks exist.
15. The production-readiness checklist is signed off.

Release A is a private internal production system. It does not authorize public property deployment.

---

# 17. Workstream I — Property, domain, and site-production workflow

## Objective

Create a provider-independent property that can present one replaceable exclusive provider.

## 17.1 Property model

Create:

```text
Property
PropertyVersion
DomainCandidate
DomainRegistration
SiteConfig
SiteBuild
Deployment
ActiveProviderAssignment
Asset
ComplianceReview
```

## 17.2 Property fields

```yaml
property:
  opportunity_id:
  neutral_brand:
  domain:
  service_family_id:
  market_id:
  public_tracking_number:
  public_contact_email:
  status:
  active_site_config_version:
  analytics_config:
```

## 17.3 Active provider model

```yaml
active_provider:
  provider_candidate_id:
  public_business_name:
  logo_asset_id:
  destination_phone:
  destination_email:
  hours:
  service_radius:
  credentials:
  license_numbers:
  approved_claims:
  attributed_testimonials:
  provider_photos:
  active_from:
  active_until:
```

Rules:

- Only one active provider assignment at a time.
- Provider-specific claims require review.
- Provider replacement preserves the property, domain, content, analytics, and tracking number.
- Provider credentials and reviews must be attributable.
- The property must disclose the referral relationship.

## 17.4 Domain workflow

```text
generate candidates locally
→ operator shortlist
→ availability check
→ operator approval
→ manual or adapter-assisted registration
→ DNS verification
```

No automatic purchase without explicit action.

## 17.5 Site configuration

SiteConfig must contain structured, editable sections:

- Brand
- Service
- Market
- Pricing guidance
- Service process
- FAQs
- Local considerations
- Provider details
- Referral disclosure
- Calls to action
- Assets
- Metadata
- Analytics
- Form routing

## 17.6 Site generation

Requirements:

- Valid HTML
- Responsive design
- Accessibility checks
- Valid sitemap XML
- Robots configuration
- Canonical URLs
- Meta descriptions
- Open Graph
- Structured data only when truthful
- No fake `LocalBusiness` identity
- Asset provenance
- Versioned deterministic builds
- Internal-link checks
- Broken-link checks
- Performance budget
- Noindex staging
- Indexable production only after approval

## 17.7 Content controls

Content must:

- Be unique to the service and market
- Be useful beyond keyword targeting
- Avoid fabricated local expertise
- Avoid unsupported price claims
- Avoid copied competitor content
- Avoid doorway-page patterns
- Clearly distinguish property content from provider-specific claims

## 17.8 Deployment

Site deployments must support:

```text
preview
staging
production
rollback
```

Every production deployment requires:

- Approved opportunity
- Approved SiteConfig version
- Active provider or approved neutral pilot mode
- Compliance review
- Domain verification
- Form/routing health check
- Analytics verification
- Operator confirmation

## Acceptance criteria

- Provider swap is configuration-only.
- Staging is always noindex.
- Production deployment is approval-gated.
- Referral disclosure is visible.
- Site claims are attributable.
- Sitemap and metadata validate.
- Builds are reproducible.
- Rollback restores the prior production version.

---

# 18. Workstream J — Lead routing, analytics, and provider operations

## Objective

Reliably route and measure leads without surrendering ownership of the property.

## 18.1 Lead model

Persist:

```text
Lead
LeadEvent
RoutingAttempt
ProviderDelivery
LeadOutcome
ConsentRecord
SpamAssessment
```

## 18.2 Form routing

Requirements:

- Server-side validation
- Spam protection
- Rate limits
- Consent text
- Referral disclosure
- Deduplication
- Idempotency key
- Delivery retry
- Operator alert on failure
- PII redaction in logs

## 18.3 Call routing

Use an adapter interface for a call-tracking provider.

Required features:

- Property-owned public number
- Provider destination number
- Call forwarding
- Routing health check
- Call metadata
- Missed-call event
- Recording only when legally and operationally approved
- Configurable retention
- Provider replacement without public-number change

## 18.4 Delivery channels

Support:

- Phone
- Email
- Webhook later
- Provider CRM later

Initial production may use phone and email only.

## 18.5 Provider assignment lifecycle

```text
candidate
pilot
active
paused
terminated
replaced
```

Track:

- Agreement dates
- Coverage
- Routing destination
- Response expectations
- Lead acceptance
- Reasons for termination
- Replacement history

## 18.6 Analytics events

Track:

```text
organic landing
form start
form submit
qualified lead
call
answered call
missed call
provider delivery
provider acceptance
appointment
won job
lost job
revenue when voluntarily reported
```

Do not claim outcomes not actually observed.

## 18.7 Privacy

Define:

- Required data only
- Consent
- Retention
- Access control
- Export
- Deletion
- Encryption
- Recording rules
- Provider access limits

## Acceptance criteria

- Test leads route successfully in staging.
- Delivery retries are idempotent.
- Provider replacement preserves the public number.
- Routing failures alert operators.
- Lead data is access-controlled.
- Consent and retention are enforced.
- Analytics distinguish observed from provider-reported outcomes.

---

# 19. Workstream K — Outcome feedback and empirical calibration

## Objective

Use real search and lead outcomes to evaluate—not blindly automate—the discovery model.

## 19.1 Outcome sources

Adapters may ingest:

- Search Console
- Web analytics
- Call tracking
- Form submissions
- Provider-reported lead status
- Provider-reported revenue
- Operator review

## 19.2 Outcome model

Persist:

```text
property_id
opportunity_id
score_version_at_selection
evidence_snapshot_id
date
impressions
clicks
average_position
organic_sessions
calls
forms
qualified_leads
appointments
won_jobs
reported_revenue
data_source
confidence
```

## 19.3 Calibration reports

Generate:

- Score versus indexing time
- Score versus impression growth
- Score versus top-10 achievement
- Component versus lead volume
- Provider-suitability score versus tenant conversion
- Addressable-market score versus lead demand
- Calibration by service family
- Calibration by market size
- Calibration by evidence quality
- Cost per validated opportunity

## 19.4 Guardrails

- Do not automatically retrain or change weights.
- Do not treat sparse outcomes as statistically conclusive.
- Distinguish correlation from causation.
- Every score change remains reviewed and versioned.
- Preserve the exact historical evidence and score used for each property decision.

## Acceptance criteria

- Property outcomes join back to the original opportunity and score version.
- Reports distinguish observed and reported outcomes.
- Weight changes require benchmark and reviewer approval.
- Historical properties can be rescored without rewriting their original decision record.

---

# 20. Workstream L — Independent QA and production validation

## Objective

Independently verify the full system before each release.

## 20.1 Functional testing

Cover:

- Prefilter
- Testing scan
- Full promotion
- Full scan
- Evidence gate
- Ranking
- Comparison
- Rescore
- Review
- Approval
- Domain workflow
- Site staging
- Provider assignment
- Deployment
- Lead routing
- Provider replacement
- Rollback

## 20.2 Load testing

Test:

- Concurrent reads
- Concurrent scan creation
- Worker concurrency
- Queue backlog
- API-call reservation
- Large replay bundles
- Batch prefilter
- Large opportunity lists
- Lead spikes

## 20.3 Failure injection

Test:

- Database interruption
- Worker crash
- Provider timeout
- Provider rate limit
- Object-store failure
- Partial response
- Duplicate webhook
- Deployment failure
- DNS failure
- Call-routing failure
- Bad scoring configuration
- Corrupted public dataset

## 20.4 Security testing

Perform:

- Authentication tests
- Authorization tests
- CSRF
- CORS
- Rate limit
- Injection
- SSRF
- File upload
- Secret exposure
- Error redaction
- Dependency scan
- Container scan

## 20.5 Data recovery test

Prove restoration of:

- PostgreSQL
- Raw responses
- Replay bundles
- Configuration versions
- Deployed site configuration
- Lead-routing configuration

## 20.6 User acceptance

The owner should complete a scripted UAT:

```text
log in
→ prefilter markets
→ run bounded testing scan
→ review preliminary result
→ promote to full
→ compare opportunities
→ approve one
→ create property
→ configure provider
→ deploy staging
→ submit test lead
→ verify delivery
→ rollback
```

## Acceptance criteria

- No unresolved P0/P1 defect.
- Restore test passes.
- Load targets pass.
- Security review passes.
- UAT passes.
- Runbooks are accurate.
- Release checklist is signed.

---

# 21. Production configuration management

Version all decision-affecting configuration:

```text
scoring
evidence quality
service catalog
SERP classification
provider suitability
public-data profiles
geography dataset
scan profiles
cost limits
site templates
lead-routing policies
```

Every scan and deployment must record the versions used.

Configuration changes require:

- Schema validation
- Diff
- Test suite
- Benchmark suite
- Reviewer approval
- Rollback target

---

# 22. Documentation requirements

Required documentation:

```text
docs/production_architecture.md
docs/deployment.md
docs/database.md
docs/backup_restore.md
docs/security.md
docs/secrets.md
docs/observability.md
docs/data_retention.md
docs/dataforseo_cost_controls.md
docs/calibration.md
docs/public_data.md
docs/opportunity_review.md
docs/property_workflow.md
docs/provider_assignment.md
docs/lead_routing.md
docs/privacy.md
docs/incident_response.md
docs/release_checklist.md
```

Documentation must describe implemented behavior, not aspirations.

---

# 23. Production readiness gates

## Gate A — Calibration ready

- Benchmark harness implemented
- Scenario library passes
- Classification benchmark passes
- Provider benchmark passes
- Configuration versioning complete

## Gate B — Paid scan ready

- Qualification current
- Per-scan and daily limits active
- Kill switch tested
- Alerts active
- Billing reconciliation working
- No unplanned network calls possible

## Gate C — Internal production ready

- PostgreSQL active
- Backup/restore tested
- Authentication active
- Audit logs active
- Observability active
- Deployment/rollback tested
- Opportunity review complete

This is Release A.

## Gate D — Property staging ready

- Approved opportunity workflow
- Property model
- Active-provider model
- Site generator hardened
- Staging noindex
- Compliance review

## Gate E — Public launch ready

- Domain verified
- Provider active
- Forms and call routing healthy
- Analytics active
- Privacy and disclosures approved
- Production rollback tested

## Gate F — Operational learning ready

- Search and lead outcomes captured
- Provider outcome workflow active
- Historical score linkage verified
- Calibration reports available

This is Release B.

---

# 24. Explicit launch blockers

Do not launch production when any of these is true:

- Calibration suite is failing.
- Production qualification is stale or failing.
- Paid-call kill switch is unavailable.
- Daily spend limits are unavailable.
- Backup restore has not been tested.
- Critical security findings are open.
- Authentication can be bypassed.
- Audit logging is incomplete.
- Evidence gate can be bypassed unintentionally.
- Opportunity lacks approval.
- Public site has no referral disclosure.
- Provider claims are unverified.
- Lead routing has not been tested.
- Rollback has not been rehearsed.
- Production health checks issue paid calls.
- Staging pages are indexable.
- Score/config versions are not persisted.

---

# 25. Recommended implementation order

## Iteration 1 — Decision-model confidence

1. Calibration harness
2. Benchmark scenarios
3. Classification/provider benchmarks
4. Evidence-gate threshold review
5. Config-version enforcement

## Iteration 2 — Production discovery foundation

1. PostgreSQL
2. Blob storage
3. Worker process
4. Daily cost controls
5. Qualification matrix
6. Billing reconciliation

## Iteration 3 — Security and operations

1. Authentication
2. Authorization
3. Audit
4. Secret store
5. Structured logs
6. Metrics/traces
7. Alerts/runbooks

## Iteration 4 — Deployment

1. Infrastructure as code
2. Staging
3. CI/CD
4. Health checks
5. Backup/restore
6. Rollback

## Iteration 5 — Review workflow

1. Opportunity states
2. Notes/ownership
3. Overrides
4. Evidence export
5. Approval gates
6. Batch prefilter/testing

**Release A gate**

## Iteration 6 — Property workflow

1. Property model
2. Domain workflow
3. Active provider
4. SiteConfig
5. Generator hardening
6. Staging deployment

## Iteration 7 — Lead operations

1. Forms
2. Call routing
3. Provider delivery
4. Analytics
5. Privacy
6. Provider replacement

## Iteration 8 — Outcome feedback

1. Search outcomes
2. Lead outcomes
3. Provider outcomes
4. Calibration reports
5. Controlled score updates

**Release B gate**

---

# 26. Definition of production-ready

The project is production-ready only when:

1. Discovery behavior is protected by benchmark scenarios.
2. Real provider calls are qualified, planned, budgeted, and reconciled.
3. Production data uses PostgreSQL and durable blob storage.
4. Backups and restores have been tested.
5. Background work is durable and observable.
6. Authentication, authorization, secrets, and audit logging are active.
7. Deployments are reproducible and reversible.
8. Opportunities move through explicit review and approval states.
9. Public property deployment is gated and compliant.
10. Provider replacement preserves the property asset.
11. Lead routing is reliable, measurable, and privacy-aware.
12. Search and lead outcomes link back to the original discovery evidence.
13. No critical security or operational blockers remain.
14. A full owner UAT succeeds.
15. Release checklists and runbooks are complete.

---

# 27. First orchestrator task

The Codex orchestrator should begin with:

> Create `docs/production_status.yaml`, assign workstreams A–L to subagents, record the current commit and verification baseline, and implement Workstream A: the offline calibration and benchmark harness. Do not begin public site deployment, lead routing, or paid production scanning until the calibration gate passes.

The first integrated pull request should include only:

- Production baseline documentation
- ADR skeletons
- Benchmark scenario schema
- Initial 20+ benchmark scenarios
- Pairwise ranking tests
- SERP classification benchmark
- Provider suitability benchmark
- Calibration CLI/report
- CI integration
- Updated production status and handoff

No production API credits should be used for this first iteration.
