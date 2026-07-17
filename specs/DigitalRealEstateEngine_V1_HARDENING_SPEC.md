# DigitalRealEstateEngine — V1 Hardening Specification

**Target repository:** `https://github.com/adammenker/DigitalRealEstateEngine`
**Baseline commit reviewed:** `c2026b64a9c66e9dd3f65fc7d286fe3691f40362`
**Audience:** Codex / coding agent
**Purpose:** Harden the current prototype into a trustworthy, cost-safe, replayable, and testable V1 discovery engine before additional DataForSEO credits are purchased.

## 1. Primary objective

The current codebase demonstrates the intended workflow but is not yet reliable enough to make investment decisions.

This hardening effort must make the system capable of:

1. Reusing already purchased API data.
2. Preventing accidental or duplicate paid calls.
3. Distinguishing preliminary scans from full opportunity assessments.
4. Producing directionally correct and explainable scores.
5. Representing national versus local demand honestly.
6. Selecting useful representative search queries before buying SERP data.
7. Preserving complete scan history and partial results.
8. Separating discovery from domains, outreach, site generation, and deployment.
9. Running scans as persisted jobs rather than long HTTP requests.
10. Safely evolving the database through migrations.
11. Testing critical behavior without network access.
12. Preparing for one tightly bounded future live qualification run.

Do not expand into production lead routing, automated sales, billing, or bulk site creation during this work.

## 2. Codex execution rules

1. Confirm the repository commit before editing.
2. Inspect the actual repository tree.
3. Create a feature branch.
4. Implement milestones in the listed order.
5. Leave the repository runnable after every milestone.
6. Keep fixture mode functional.
7. Do not make live DataForSEO calls by default.
8. Never silently fall back from live mode to fixture or replay mode.
9. Do not weaken tests to force a pass.
10. Add tests for each behavioral change.
11. Use Alembic migrations for schema changes.
12. Record deviations in `docs/hardening_deviations.md`.
13. Preserve all existing raw API responses.
14. Never present incomplete or synthetic data as a full live score.
15. Never consume paid API credits in CI.
16. Run the full verification suite after every milestone.

## 3. Required operating modes

Normalize the application around:

```text
fixture
replay
live
```

### Fixture

- Deterministic synthetic data.
- No network calls.
- Default for tests and CI.
- Results visibly labeled synthetic.

### Replay

- Previously stored or exported live API responses.
- Runs through real normalizers.
- No network calls.
- Shows source scan and source timestamp.

### Live

- Real adapters only.
- Explicit opt-in.
- Fails fast without credentials.
- Never falls back silently.
- Requires passed qualification.
- Respects cost and request limits.

Every `ScanRun` must store:

```text
data_mode
scan_profile
adapter_names
adapter_versions
normalization_version
scoring_version
cache_policy_version
planned_cost_usd
actual_cost_usd
source_scan_run_id
started_at
completed_at
```

Acceptance:

- Fixture/replay tests make zero network calls.
- Live mode without credentials fails before creating a request.
- UI and API expose the active mode.
- Tests prove live mode cannot instantiate mock adapters.

## 4. Milestone 0 — Baseline verification

Tasks:

1. Record branch, commit SHA, Python, Node, Docker, and package-manager versions.
2. Run backend tests, Ruff, mypy, frontend type checking, frontend build, and Docker build.
3. Add or update `make verify`.
4. Create `docs/hardening_baseline.md` describing:
   - repository structure
   - adapters
   - fixtures
   - live capabilities
   - failing checks
   - database behavior
   - scoring version
   - scan lifecycle
   - generated-site behavior

Acceptance:

- Clean checkout installation is documented.
- One command runs all offline checks.
- Existing failures are documented before modification.

## 5. Milestone 1 — Stored API responses

Create a durable representation:

```python
class StoredApiResponse(BaseModel):
    provider: str
    endpoint: str
    api_version: str
    response_shape_version: str
    normalized_request: dict[str, Any]
    raw_response: dict[str, Any]
    sanitized: bool
    provider_task_id: str | None
    provider_request_id: str | None
    provider_cost_usd: Decimal | None
    requested_at: datetime
    received_at: datetime
    source_scan_run_id: UUID | None
    checksum: str
```

Tasks:

- Persist every DataForSEO response.
- Remove credentials before storage.
- Normalize requests deterministically.
- Generate request/response checksums.
- Record task IDs, request IDs, endpoint, API version, cost, and timestamps.
- Add migrations and repository methods.
- Import or preserve existing raw responses.

Acceptance:

- Responses can be found by normalized request.
- Stored payloads contain no credentials.
- Modified fixtures fail checksum validation.
- Tests cover serialization and sanitization.

## 6. Milestone 2 — Replay mode

Commands:

```bash
rank-rent replay scan <scan_run_id>
rank-rent replay bundle <bundle_path>
rank-rent fixtures export <scan_run_id> --output <path>
rank-rent fixtures validate <bundle_path>
```

Tasks:

- Add replay dependency wiring.
- Feed recorded responses through live normalizers.
- Support replay by request, scan run, and fixture bundle.
- Rerun normalization, filtering, classification, scoring, and dashboard rendering.
- Export sanitized bundles.
- Validate checksums and schema versions.
- Fail clearly when required responses are absent.

Acceptance:

- Replay makes zero network calls.
- Replay normalized output matches the original.
- Scoring changes can be tested against recorded data.
- Corrupted bundles are rejected.
- Replay E2E tests pass in CI.

## 7. Milestone 3 — Cache integration

Cache key:

```text
provider + endpoint + API version + response shape version + normalized request
```

Suggested TTLs:

```text
Location catalog:     90 days
Location lookup:      30 days
Keyword suggestions:  30 days
Keyword metrics:      30 days
Business listings:    10 days
Backlink summaries:   45 days
SERP snapshots:       immutable historical records
```

Tasks:

- Wrap every DataForSEO call with cache lookup.
- Store hit/miss, timestamps, expiry, original cost, and source scan.
- Preserve SERP snapshots.
- Add force refresh with explicit confirmation.
- Add cache metrics to scans.
- Make rescoring read stored normalized data.
- Add safe cleanup tooling.

Acceptance:

- Identical repeated scans reuse eligible responses.
- Rescoring makes zero calls.
- Expired data is visible.
- Force refresh is explicit.
- Tests cover hit, miss, expiry, and refresh.

## 8. Milestone 4 — Scan planning and cost controls

Workflow:

```text
Input
→ normalize service
→ resolve geography
→ choose profile
→ plan endpoints
→ evaluate cache
→ estimate uncached cost
→ confirm
→ execute
```

Models:

```python
class PlannedApiCall(BaseModel):
    provider: str
    endpoint: str
    stage: str
    request_parameters: dict[str, Any]
    cache_key: str
    cache_hit: bool
    required: bool
    estimated_cost_usd: Decimal

class ScanPlan(BaseModel):
    scan_profile: str
    calls: list[PlannedApiCall]
    cache_hit_count: int
    paid_call_count: int
    estimated_uncached_cost_usd: Decimal
    maximum_allowed_cost_usd: Decimal
    confirmation_required: bool
    blocked: bool
    block_reason: str | None
```

Tasks:

- Build the plan before execution.
- Estimate only uncached calls.
- Remove hard-coded cost estimates.
- Display endpoint-level calls in CLI/UI.
- Enforce maximum cost and maximum request count.
- Make dry run incapable of network calls.
- Store plan and actual cost.
- Stop optional stages when budget is insufficient.
- Reconcile estimated and actual costs.

Acceptance:

- Dry run makes zero calls.
- Over-budget scans make zero paid calls.
- User sees endpoint counts and cache hits.
- Tests cover over-budget, partial-budget, billing failure, cached scans, and force refresh.

## 9. Milestone 5 — Separate scan profiles

Profiles:

```text
testing
full
```

Testing produces `PreliminaryOpportunityAssessment`, not `FullOpportunityScore`.

Testing may use:

- limited seeds
- limited metrics
- one representative SERP
- limited providers
- no backlink metrics

Full scans require the configured full evidence set or an explicit partial status.

Tasks:

- Add distinct models and persistence.
- Update UI labels.
- Prevent preliminary and full results from sharing an unqualified ranked list.
- Add promotion:
  `testing → review → approve full scan → full score`
- Prevent high confidence for preliminary assessments.
- Display missing full-scan components.

Acceptance:

- Testing cannot create a full score.
- Preliminary output is labeled.
- Results are not directly comparable.
- Tests cover promotion and missing components.

## 10. Milestone 6 — Scoring rewrite

Use components:

```text
Demand evidence
Commercial value
Competitor weakness
Organic click availability
Provider suitability
Data completeness
```

Required invariants:

1. Stronger competitors lower competitor weakness.
2. Greater local relevance among existing competitors lowers attractiveness.
3. More referring domains never improve competitor weakness.
4. Strong directories and national brands reduce attractiveness.
5. Local packs never improve organic click availability.
6. Ads and SERP displacement never improve organic click availability.
7. CPC influences commercial value, not organic difficulty.
8. Missing core components prevent high confidence.
9. One minor missing field does not equal several missing core components.
10. Adding valid data cannot reduce completeness confidence.

Move all weights, thresholds, caps, normalizers, provider ranges, CPC ranges, authority cutoffs, and penalties into versioned YAML.

Persist:

```text
scoring version
component inputs
availability states
formulas
outputs
penalties
confidence
explanation
source scan
source timestamps
```

Acceptance:

- Every invariant has tests.
- Stronger competitor fixtures never improve scores.
- Dominant local packs never improve click availability.
- Rescoring makes zero calls.
- Historical scoring versions remain queryable.

## 11. Milestone 7 — Keyword pipeline hardening

Workflow:

```text
seed queries
→ candidate discovery
→ normalization
→ negative filtering
→ metrics
→ close-variant clustering
→ value ranking
→ representative SERP selection
```

Tasks:

- Move service-specific modifiers out of shared adapter code.
- Extend service seeds with `intent_modifiers` and `negative_terms`.
- Remove unrelated hard-coded keyword penalties.
- Add exact deduplication.
- Add conservative close-variant grouping.
- Do not blindly sum grouped variants.
- Rank candidates by service relevance, transactional intent, CPC, volume, and local modifier quality.
- Select SERP queries only after metrics.
- Persist included, excluded, grouped, and representative decisions.

Acceptance:

- SERP queries are selected by value, not input order.
- Service-specific modifiers live in seed configuration.
- Close variants are not double-counted.
- Tests cover grouping, exclusion, ranking, and representative selection.

## 12. Milestone 8 — Offline geographic resolution

Interface:

```python
class GeographicResolver(Protocol):
    def resolve(
        self,
        query: str,
        country_code: str = "US",
    ) -> GeographicMarket:
        ...
```

Required fields:

```text
original_input
normalized_city
state
county
metro
postal_code
latitude
longitude
radius_miles
country_code
resolution_confidence
dataforseo_location_code
dataforseo_location_name
keyword_metric_granularity
serp_granularity
provider_granularity
```

Tasks:

- Add an offline U.S. city/ZIP dataset.
- Resolve city, ZIP, county, and custom market.
- Populate coordinates.
- Remove hard-coded city exceptions.
- Make V1 explicitly U.S.-only.
- Preserve original input.
- Require confirmation for ambiguity.
- Use coordinates for SERPs/providers and broader supported geography for keyword metrics.

Acceptance:

- Common city/ZIP resolution uses no SEO calls.
- Provider searches receive coordinates.
- Ambiguous markets are not guessed silently.
- Tests cover all supported location types.

## 13. Milestone 9 — Demand granularity honesty

Store separately:

```text
national_service_demand
provider_reported_metric_granularity
estimated_market_demand
market_estimation_method
market_estimation_confidence
localized_competition
localized_provider_supply
```

Tasks:

- Preserve raw national metrics.
- Label returned geography accurately.
- Never relabel national data as city data.
- Optionally estimate local demand from offline geography/population signals.
- Clearly distinguish raw and estimated values.
- Record estimation method and confidence.
- Show UI warnings for estimates.

Acceptance:

- National volume is never displayed as exact city demand.
- Estimated demand is labeled.
- Every value carries source and granularity.
- Tests cover national, metro, city, and estimated values.

## 14. Milestone 10 — Qualification harness

Capabilities:

```text
account access
location lookup
keyword discovery
keyword metrics
monthly history
SERP parsing
SERP features
business listings
backlinks
partial task handling
billing failure
rate limiting
schema drift
```

States:

```text
passed
partial
no_data
unauthorized
rate_limited
billing_error
schema_mismatch
failed
not_configured
```

Tasks:

- Replace static pass reports.
- Run real normalizers against fixture and replay data.
- Validate with Pydantic.
- Store endpoint, task ID, request ID, counts, missing fields, and schema version.
- Block live scans when required capabilities fail.
- Make future live qualification opt-in, bounded, and saved as replay fixtures.

Acceptance:

- Fixture and replay qualification validate every parser offline.
- Missing required fields produce `schema_mismatch`.
- Paid calls never occur by default.

## 15. Milestone 11 — Workflow separation

Lifecycle:

```text
scan
→ preliminary/full review
→ approve
→ generate domains
→ shortlist providers
→ generate outreach
→ create SiteConfig
→ build preview
→ deploy staging
```

Tasks:

- Remove domain generation, outreach, SiteConfig creation, and site writes from scanning.
- Add explicit idempotent actions for each downstream step.
- Validate lifecycle transitions.
- Preserve provider independence.

Acceptance:

- Scans create no site files.
- Outreach and SiteConfig require approval.
- Scan retries do not duplicate downstream records.
- Invalid transitions are rejected.

## 16. Milestone 12 — Failure and partial-result handling

Opportunity states:

```text
discovered
scan_queued
scan_running
preliminary_review
full_review
partial_review
scan_failed
approved
rejected
```

Tasks:

- Mark failed scans correctly.
- Preserve previous valid scores after failed rescans.
- Preserve partial normalized data.
- Store stage-specific errors.
- Add retry metadata.
- Add score validity fields.

Acceptance:

- Failed and partial results are distinct.
- Failed rescans do not replace valid scores.
- Partial data remains inspectable.
- Tests cover failure at each stage.

## 17. Milestone 13 — Persisted asynchronous scans

API:

```text
POST /api/scans
GET /api/scans/{scan_run_id}
POST /api/scans/{scan_run_id}/cancel
POST /api/scans/{scan_run_id}/retry
```

Stages:

```text
queued
resolving_location
planning
discovering_keywords
fetching_metrics
fetching_serps
fetching_competitors
fetching_providers
scoring
completed
partial
failed
cancelled
```

Tasks:

- Persist jobs before execution.
- Use a database-backed in-process worker.
- Persist progress, stage, partial outputs, and incremental cost.
- Support browser refresh.
- Add idempotent retries.
- Prevent duplicate processing.
- Add cancellation.

Acceptance:

- Scan creation returns immediately.
- Progress can be polled.
- Two workers cannot process the same scan.
- Cancellation prevents future paid stages when possible.
- Tests cover locking, retry, cancellation, and restart behavior.

## 18. Milestone 14 — Database hardening

Tasks:

- Add Alembic baseline migration.
- Remove startup `create_all()`.
- Add typed tables for:
  - ScanPlan
  - PlannedApiCall
  - ApiCall
  - StoredApiResponse
  - KeywordCluster
  - KeywordMetric
  - KeywordDecision
  - SerpSnapshot
  - SerpResult
  - CompetitorMetric
  - ProviderCandidate
  - PreliminaryAssessment
  - FullOpportunityScore
  - ScoreComponent
  - DomainCandidate
  - OutreachDraft
  - SiteConfig
  - Asset
  - Deployment
- Keep raw payload references.
- Add keys, indexes, and uniqueness on canonical service family plus market.
- Add migration tests using populated fixtures.

Acceptance:

- `alembic upgrade head` creates the schema.
- Existing data survives migration.
- Startup does not mutate schema.
- Duplicate opportunities are prevented.

## 19. Milestone 15 — Site generator hardening

Only after workflow separation:

- Generate valid XML sitemap.
- Add base URL, canonicals, descriptions, Open Graph metadata.
- Add `noindex` to local and staging builds.
- Render approved assets only.
- Persist asset provenance.
- Remove duplicate generic sections.
- Disable or safely stub staging forms.
- Include referral disclosure.
- Avoid false `LocalBusiness` schema.
- Validate HTML and internal links.
- Test provider independence.
- Version builds by SiteConfig version.

Acceptance:

- Site generation requires approval.
- Sitemap is valid XML.
- Staging is `noindex`.
- Broken links fail builds.
- No provider identity leaks.

## 20. Milestone 16 — Seed validation

Use top-level Pydantic models.

Validate:

```text
duplicate IDs
duplicate slugs
empty seed queries
invalid intent modifiers
invalid negative terms
malformed ZIP codes
invalid coordinates
unsupported countries
empty custom markets
duplicate market members
invalid provider categories
```

Acceptance:

- Errors include exact paths.
- Loading is idempotent.
- Invalid files cause no partial writes.
- Tests cover multiple simultaneous errors.

## 21. Milestone 17 — CI and reproducibility

Python:

- Commit `uv.lock`.
- Use frozen installs.
- Pin Python version.
- Run Ruff, mypy, and pytest.

Frontend:

- Commit lockfile.
- Use `npm ci`.
- Run type check and production build.

Docker:

- Run as non-root.
- Add health checks.
- Do not bake secrets.
- Parameterize:

```yaml
APP_DATA_MODE: ${APP_DATA_MODE:-fixture}
ALLOW_LIVE_API_CALLS: ${ALLOW_LIVE_API_CALLS:-false}
MAX_SCAN_COST_USD: ${MAX_SCAN_COST_USD:-10.00}
```

GitHub Actions:

```text
backend checks
frontend checks
Docker build
fixture E2E
replay contract test
migration test
```

Never run paid tests automatically.

Acceptance:

- Clean checkout builds reproducibly.
- CI passes without credentials.
- Fixture/replay CI jobs make zero network calls.

## 22. Required test coverage

Add tests for:

1. Fixture/replay/live separation
2. Missing live credentials
3. Replay integrity and normalization
4. Cache hit/miss/expiry/refresh
5. Dry run zero calls
6. Over-budget zero calls
7. Estimated versus actual cost
8. Preliminary/full separation
9. Scoring directionality
10. Missing-data confidence
11. Exact and close keyword deduplication
12. Representative SERP selection
13. National versus local labels
14. City, ZIP, county, and custom-market resolution
15. Ambiguous locations
16. Partial tasks
17. Billing failures
18. Rate limits
19. Schema drift
20. Failed and partial lifecycle
21. Async locking, retry, cancellation
22. Opportunity uniqueness
23. No site or outreach before approval
24. Sitemap and staging `noindex`
25. Provider independence
26. Seed validation
27. Alembic upgrade
28. Fixture E2E
29. Replay E2E

## 23. Implementation sequence

Implement in this order:

1. Baseline verification
2. Stored responses
3. Replay mode
4. Cache integration
5. Scan planning and budget enforcement
6. Preliminary/full separation
7. Scoring rewrite
8. Keyword pipeline
9. Geographic resolution
10. Demand granularity
11. Qualification harness
12. Workflow separation
13. Failure lifecycle
14. Async jobs
15. Alembic and typed persistence
16. Site generator
17. Seed validation
18. CI/reproducibility

Do not use more DataForSEO credits until milestones 1–11 are complete and passing offline.

## 24. Definition of done

Hardening is complete when:

1. Fixture and replay modes make zero network calls.
2. Purchased responses can be replayed.
3. Repeat scans use cache.
4. Rescoring uses no paid calls.
5. Dry runs and over-budget scans make no paid calls.
6. Testing scans never produce full scores.
7. Scoring direction is protected by tests.
8. National demand is not mislabeled as local.
9. U.S. cities and ZIPs resolve offline.
10. Qualification validates parsers offline.
11. Discovery does not generate sites or outreach.
12. Failed scans are not shown as successful reviews.
13. Scans run as persisted jobs.
14. Core research data lives in migrated typed tables.
15. CI passes without credentials.
16. The next live qualification run can use one request per capability and save every response for replay.

## 25. Explicit non-goals

Do not implement:

- Google Ads API
- Google Places API
- Twilio
- automatic email sending
- provider billing
- production lead routing
- backlink automation
- ML scoring
- bulk city pages
- automatic domain purchase
- automatic production deployment
- automated pricing negotiation

## 26. First Codex task

Start with:

> Implement persisted sanitized API-response storage, network-free replay mode, and cache wrapping for every DataForSEO endpoint. Add tests proving fixture mode, replay mode, dry runs, and rescoring make zero network calls. Do not change scoring, site generation, or downstream workflows in this first task.

After this passes, implement scan planning and cost enforcement.
