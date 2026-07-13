# DigitalRealEstateEngine — Offline Remediation Specification

**Target repository:** `https://github.com/adammenker/DigitalRealEstateEngine`  
**Reviewed commit:** `c2026b64a9c66e9dd3f65fc7d286fe3691f40362`  
**Audience:** Codex / coding agent  
**Primary goal:** Improve correctness, cost safety, testability, and architecture before making additional paid DataForSEO calls.

---

## 1. Scope

This specification covers code improvements that can be implemented and tested primarily offline.

The current repository has a useful fixture-driven foundation and an initial live DataForSEO path, but it must not be trusted for opportunity ranking until the issues below are fixed.

The remediation must focus on:

1. Preventing accidental paid API calls.
2. Making cached and recorded API data reusable.
3. Separating testing scans from full opportunity scores.
4. Correcting scoring direction and confidence behavior.
5. Improving keyword selection and local-market modeling.
6. Separating discovery from site generation and outreach.
7. Adding reliable persistence, migrations, and job execution.
8. Expanding tests before purchasing more API credits.

Do not add unrelated V2 business features during this work.

---

## 2. Codex operating rules

1. Begin by checking out and confirming commit:
   `c2026b64a9c66e9dd3f65fc7d286fe3691f40362`.
2. Inspect the actual repository before changing files.
3. Implement milestones in the order listed.
4. Keep fixture mode working after every milestone.
5. Do not make live DataForSEO calls unless explicitly enabled.
6. Do not weaken existing tests to make them pass.
7. Add tests for every behavioral change.
8. Use Alembic migrations for database changes.
9. Keep the repository runnable after each milestone.
10. Record deviations in `docs/implementation_deviations.md`.
11. Never silently substitute fixtures when live mode is requested.
12. Never present incomplete testing-mode results as equivalent to a full scan.
13. Do not implement production lead routing, billing, or automated outreach.
14. Prefer simple, testable abstractions over premature generalization.
15. Preserve raw API payloads already captured by the application.

---

## 3. Required application modes

Introduce or normalize an explicit mode enum:

```text
fixture
live
replay
```

### Fixture mode

- Uses deterministic synthetic fixture providers.
- Makes no network calls.
- Is the default for tests and CI.
- Must clearly label all scan and score output as synthetic.

### Replay mode

- Reads previously stored DataForSEO responses.
- Runs the real normalization and scoring code.
- Makes no network calls.
- Is used to improve parsing, scoring, and UI without purchasing more data.

### Live mode

- Uses production adapters.
- Requires explicit credentials and configuration.
- Must fail fast if credentials are absent.
- Must never fall back silently to fixtures or replay data.

### Required persistence fields

Every `ScanRun` must store:

- `data_mode`
- adapter names
- adapter versions
- scoring version
- cache policy version
- started timestamp
- completed timestamp
- actual API cost, when known

### Acceptance criteria

- Tests prove fixture and replay modes make zero network calls.
- Starting live mode without credentials fails with an actionable error.
- UI and API responses expose the active data mode.
- Fixture scores display a visible “synthetic data” warning.
- Replay scans display the original source timestamp of the recorded data.

---

## 4. Milestone 0 — Establish a verified baseline

### Tasks

1. Record:
   - branch
   - commit SHA
   - Python version
   - Node version
   - package manager versions
2. Run:
   - backend tests
   - Ruff
   - mypy
   - frontend type check
   - frontend production build
   - Docker build
3. Document failures before modifying code.
4. Add a single verification command, for example:

```bash
make verify
```

or:

```bash
uv run python scripts/verify_repo.py
```

5. Add `docs/current_state.md` containing:
   - current repository structure
   - current adapters
   - current fixtures
   - known failing checks
   - known live capabilities
   - current scoring version
   - current database initialization behavior

### Acceptance criteria

- A clean checkout can be installed using documented commands.
- One command runs the full offline verification suite.
- Existing failures are documented rather than hidden.
- The repository state and commit are recorded.

---

## 5. Milestone 1 — Recorded-response replay layer

This is the highest-priority credit-saving change.

### Objective

Make previously purchased DataForSEO responses reusable through the same normalization path as live responses.

### Required design

Create a provider-neutral stored response representation:

```python
class StoredApiResponse(BaseModel):
    provider: str
    endpoint: str
    api_version: str
    normalized_request: dict[str, Any]
    response_body: dict[str, Any]
    provider_task_id: str | None
    provider_cost_usd: Decimal | None
    requested_at: datetime
    received_at: datetime
    source_scan_run_id: UUID | None
    checksum: str
```

Create a replay transport:

```python
class ReplayTransport(Protocol):
    async def get_response(
        self,
        provider: str,
        endpoint: str,
        normalized_request: dict[str, Any],
    ) -> StoredApiResponse: ...
```

### Tasks

1. Persist raw responses from every DataForSEO endpoint.
2. Sanitize secrets before storage.
3. Add deterministic request normalization.
4. Add checksums to detect fixture corruption.
5. Build replay adapters that feed stored responses into the existing live normalizers.
6. Allow replay by:
   - exact request key
   - scan run
   - fixture bundle
7. Add commands:

```bash
rank-rent replay scan <scan_run_id>
rank-rent replay bundle <bundle_path>
rank-rent fixtures export <scan_run_id> --output fixtures/recorded/
```

8. Ensure replay runs can:
   - rerun normalization
   - rerun classification
   - rerun scoring
   - regenerate dashboard output
   - make zero paid calls

### Acceptance criteria

- Replay mode produces the same normalized result as the original stored response.
- Replay runs make zero network calls.
- Scoring changes can be tested against recorded responses without DataForSEO credits.
- Stored responses contain no credentials.
- Contract fixtures can be exported from sanitized responses.

---

## 6. Milestone 2 — Real cache integration

A cache class exists, but all paid requests must actually use it.

### Required cache key

```text
provider
+ endpoint
+ API version
+ normalized parameters
+ response-shape version
```

### Suggested TTLs

```text
Location catalog / location lookup: 30–90 days
Keyword suggestions:              30 days
Keyword metrics:                  30 days
Business/provider listings:        7–14 days
Backlink summaries:               30–60 days
SERP snapshots:                   immutable historical snapshots
```

### Tasks

1. Add a cached request wrapper around every DataForSEO call.
2. Record:
   - cache key
   - hit/miss
   - source timestamp
   - expiration timestamp
   - original cost
   - source scan
3. Preserve SERP history rather than overwriting it.
4. Add explicit force-refresh support.
5. Require cost confirmation before force-refresh.
6. Make rescoring consume normalized stored data only.
7. Add cache metrics to logs and scan output.

### Acceptance criteria

- Repeating an identical scan reuses all eligible cached data.
- Rescoring makes zero API calls.
- Expired cache entries are visible before refresh.
- Historical SERP snapshots remain queryable.
- Tests cover hits, misses, expiry, and force refresh.
- Cache behavior is deterministic across fixture, replay, and live modes.

---

## 7. Milestone 3 — Scan planning and cost enforcement

The current hard-coded estimates must be replaced.

### Required workflow

```text
User input
→ service normalization
→ location resolution
→ planned API calls
→ cache lookup
→ uncached request count
→ estimated cost
→ confirmation
→ execution
```

### ScanPlan model

```python
class PlannedApiCall(BaseModel):
    provider: str
    endpoint: str
    request_parameters: dict[str, Any]
    cache_key: str
    cache_hit: bool
    estimated_cost_usd: Decimal
    required: bool
    stage: str

class ScanPlan(BaseModel):
    scan_profile: str
    planned_calls: list[PlannedApiCall]
    cached_cost_usd: Decimal
    estimated_uncached_cost_usd: Decimal
    maximum_allowed_cost_usd: Decimal
    blocked: bool
    block_reason: str | None
```

### Tasks

1. Build a scan plan before execution.
2. Estimate only uncached request cost.
3. Show the endpoint-level plan in CLI and UI.
4. Enforce `MAX_SCAN_COST_USD`.
5. Add a lower confirmation threshold.
6. Abort before any paid request when over budget.
7. Track actual cost from DataForSEO responses.
8. Stop scheduling optional stages when the remaining budget is insufficient.
9. Make dry-run mode incapable of making live requests.

### Acceptance criteria

- Dry run makes zero network calls.
- A scan over budget makes zero paid calls.
- UI shows planned endpoint counts and cache hits.
- Actual cost is stored after execution.
- Hard-coded fixture estimates are removed.
- Tests simulate billing, rate-limit, and partial-budget cases.

---

## 8. Milestone 4 — Separate testing scans from full opportunity scores

The low-cost testing profile must not produce a normal full score.

### Required result types

```text
PreliminaryOpportunityAssessment
FullOpportunityScore
```

### Preliminary assessment

May use:

- limited keyword metrics
- one representative SERP
- limited provider listings
- no backlink data

It must show:

- available components
- unavailable components
- preliminary confidence
- expected additional calls required for a full scan
- no score directly comparable to full opportunity scores

### Full score

Requires:

- complete configured keyword analysis
- representative SERPs
- competitor metrics
- provider analysis
- all required scoring components or explicit partial status

### Tasks

1. Add distinct models and database records.
2. Add a `scan_profile` enum:
   - `testing`
   - `full`
3. Prevent sorting preliminary and full scores in the same ranked list without clear separation.
4. Update UI labels.
5. Update scoring logic to reject insufficient inputs for a full score.
6. Add a promotion workflow:

```text
testing assessment
→ review
→ request full scan
→ full score
```

### Acceptance criteria

- Testing scans cannot create `FullOpportunityScore`.
- Preliminary output clearly lists missing full-scan capabilities.
- Provider limits in testing mode do not distort a full scoring formula.
- Tests prove testing and full results are not directly comparable.
- High confidence is impossible for a preliminary assessment.

---

## 9. Milestone 5 — Rewrite and version scoring

### New scoring components

```text
Demand evidence
Commercial value
Competitor weakness
Organic click availability
Provider suitability
Data completeness
```

### Directionality invariants

1. Stronger competitors must not improve `Competitor weakness`.
2. Higher local relevance among ranking competitors must reduce opportunity attractiveness.
3. More referring domains must not increase `Competitor weakness`.
4. More ads, maps, directories, and SERP displacement must not improve `Organic click availability`.
5. Missing a core component must prevent high confidence.
6. Adding valid data must not lower completeness confidence.
7. CPC is a commercial signal, not an organic difficulty signal.
8. Testing-profile limits must not silently alter the full formula.

### Configuration

Move into versioned YAML:

- component weights
- normalizers
- thresholds
- caps
- authority cutoffs
- provider-count ranges
- CPC ranges
- demand ranges
- missing-data penalties
- confidence thresholds

Example:

```yaml
version: 2
components:
  demand_evidence:
    weight: 25
    volume_normalizer: 900
  commercial_value:
    weight: 15
    cpc_normalizer: 25
  competitor_weakness:
    weight: 25
  organic_click_availability:
    weight: 20
  provider_suitability:
    weight: 15
confidence:
  high:
    min_completeness: 0.9
  medium:
    min_completeness: 0.65
```

### Required score persistence

Store:

- scoring version
- component inputs
- component formulas
- component outputs
- missing-data state
- confidence
- explanation
- source scan
- source timestamps

### Acceptance criteria

- Unit tests cover every directionality invariant.
- A stronger competitor fixture never improves the score.
- Adding a dominant local pack never improves organic click availability.
- Missing one minor field does not receive the same penalty as several missing core components.
- Full rescoring works from stored data with zero API calls.
- Previous score versions remain queryable.

---

## 10. Milestone 6 — Keyword pipeline improvements

### Current problem

Keyword ordering and generic hard-coded modifiers can waste SERP calls and distort demand.

### Required workflow

```text
seed queries
→ candidate discovery
→ normalization
→ negative filtering
→ metrics retrieval
→ close-variant clustering
→ intent/value ranking
→ representative SERP selection
```

### Tasks

1. Move generic intent modifiers out of the DataForSEO adapter.
2. Add service-specific seed configuration:

```yaml
intent_modifiers:
  - repair
  - replacement
  - emergency
  - installation
negative_product_terms:
  - parts
  - kit
  - manual
```

3. Remove unrelated hard-coded terms such as product-specific penalties from shared adapter logic.
4. Add normalized exact deduplication.
5. Add conservative close-variant clustering.
6. Prevent blind summation of near-identical keyword variants.
7. Rank SERP candidates using:
   - commercial intent
   - CPC
   - volume
   - relevance to service family
   - local modifier quality
8. Store why each keyword was:
   - included
   - excluded
   - grouped
   - selected for SERP analysis

### Acceptance criteria

- Representative SERP queries are selected after metrics, not from original list order.
- Industry-specific terms are configured in service seeds.
- Close variants are not blindly counted as separate full demand pools.
- Tests cover grouping and selection.
- Keyword decisions are visible in the opportunity detail page.

---

## 11. Milestone 7 — Geographic resolution and local-demand honesty

### Objective

Do not present national keyword metrics as exact local city volume.

### GeographicResolver interface

```python
class GeographicResolver(Protocol):
    def resolve(self, query: str, country_code: str) -> GeographicMarket: ...
```

### GeographicMarket fields

- original input
- normalized city
- state
- county
- metro
- postal code
- latitude
- longitude
- radius
- country code
- resolution confidence
- DataForSEO location identifiers
- keyword metric granularity
- SERP granularity
- provider-search granularity

### Tasks

1. Add an offline U.S. city/ZIP dataset.
2. Resolve common U.S. city and ZIP inputs without using SEO credits.
3. Populate coordinates for provider and localized SERP research.
4. Remove hard-coded coordinate exceptions.
5. Make V1 explicitly U.S.-only unless a country has complete support.
6. Preserve national keyword metrics separately.
7. Label demand data as:
   - national
   - metro
   - city
   - estimated market demand
8. If local search volume is unavailable:
   - show national raw value
   - show a clearly labeled market estimate
   - record estimation method and confidence
9. Do not imply exact ZIP-level keyword volume.

### Acceptance criteria

- City and ZIP resolution works offline.
- Provider discovery receives valid coordinates for supported markets.
- Dashboard clearly labels metric granularity.
- National volume is never displayed as city-level volume.
- Tests cover city, ZIP, county, custom market, and invalid input.

---

## 12. Milestone 8 — Offline qualification harness

Build a comprehensive qualification harness that can run from fixtures and replay data now, then perform minimal live checks later.

### Required capabilities

```text
account access
location resolution
keyword discovery parsing
keyword metric parsing
SERP parsing
SERP feature parsing
competitor/backlink parsing
business listing parsing
partial task handling
billing failure handling
rate limit handling
schema drift detection
```

### Qualification states

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

### Tasks

1. Replace static pass declarations.
2. Run real normalization logic against fixture/replay responses.
3. Validate expected fields through Pydantic models.
4. Store endpoint, request ID, task ID, result count, and missing fields.
5. Make live qualification opt-in and cost bounded.
6. Allow exactly one request per capability during a future live run.
7. Save successful live responses as sanitized fixtures.

### Acceptance criteria

- `qualify --fixtures` validates all parser paths offline.
- `qualify --replay` validates stored live responses offline.
- A missing required field creates `schema_mismatch`.
- Live scanning is blocked when required capabilities are not qualified.
- The harness makes no paid calls by default.

---

## 13. Milestone 9 — Separate discovery from downstream workflows

### Required lifecycle

```text
scan
→ preliminary/full review
→ approve
→ generate domain candidates
→ shortlist providers
→ generate outreach
→ configure site
→ generate preview
→ deploy staging
```

### Tasks

1. Remove site generation from the scan pipeline.
2. Remove outreach generation from the scan pipeline.
3. Do not generate provider drafts until an opportunity is approved.
4. Do not create SiteConfig automatically during scanning.
5. Add explicit service methods/actions:
   - `approve_opportunity`
   - `generate_domain_candidates`
   - `shortlist_providers`
   - `generate_outreach`
   - `create_site_config`
   - `build_site_preview`
6. Make each step idempotent.
7. Add lifecycle validation.

### Acceptance criteria

- A completed scan writes no generated site files.
- Outreach drafts do not exist before approval.
- SiteConfig does not exist before approval.
- Retrying a scan does not duplicate downstream records.
- Tests cover invalid lifecycle transitions.

---

## 14. Milestone 10 — Failure and partial-result lifecycle

### Required opportunity states

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

### Tasks

1. Mark failed scans as `scan_failed`.
2. Mark incomplete but usable scans as `partial_review`.
3. Do not write a new latest score unless scoring completed.
4. Preserve partial normalized data.
5. Store stage-specific errors.
6. Add retry metadata.
7. Ensure a failed scan never becomes ordinary `review_required`.

### Acceptance criteria

- Failure states are distinct from partial states.
- Latest valid score remains unchanged after a failed rescan.
- Partial data is inspectable.
- Tests cover failure at every major stage.

---

## 15. Milestone 11 — Persisted asynchronous scan jobs

Do not hold the HTTP request open while scanning.

### Required endpoints

```text
POST /api/scans
GET /api/scans/{scan_run_id}
POST /api/scans/{scan_run_id}/cancel
POST /api/scans/{scan_run_id}/retry
```

### Required stages

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

### Tasks

1. Persist a job before execution.
2. Use a database-backed in-process worker for V1.
3. Persist progress and stage.
4. Support browser refresh.
5. Add stage-aware idempotent retry.
6. Prevent duplicate workers from taking the same job.
7. Allow cancellation before the next paid stage.
8. Persist actual API cost incrementally.

### Acceptance criteria

- Scan creation returns immediately.
- Progress is queryable.
- Restarting the web process does not corrupt state.
- Cancellation prevents future stages where possible.
- Concurrent processing of the same job is prevented.

---

## 16. Milestone 12 — Alembic migrations and typed persistence

### Tasks

1. Add an Alembic baseline migration.
2. Remove normal startup use of `Base.metadata.create_all()`.
3. Add typed tables for:
   - ScanPlan
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
   - Deployment
4. Retain raw JSON references.
5. Add foreign keys and indexes.
6. Add uniqueness on canonical:
   `service_family_id + market_id`.
7. Add migration tests against an existing populated database fixture.

### Acceptance criteria

- `alembic upgrade head` creates the full schema.
- Existing data survives migration.
- Core filtering does not require parsing generic JSON.
- Concurrent duplicate opportunities are prevented.
- Startup never modifies the schema automatically.

---

## 17. Milestone 13 — Site generator correctness

Do this after lifecycle separation.

### Tasks

1. Generate valid XML sitemap.
2. Add configurable base URL.
3. Add canonical links.
4. Add meta descriptions.
5. Add Open Graph metadata.
6. Add `noindex` for local/staging builds.
7. Render approved assets only.
8. Store asset source and attribution.
9. Remove duplicate generic sections.
10. Disable the contact form in staging or connect it to an explicit safe stub.
11. Add referral disclosure.
12. Avoid false `LocalBusiness` schema.
13. Add HTML validation.
14. Add internal-link validation.
15. Add tests proving provider independence.

### Acceptance criteria

- Site generation occurs only after approval.
- Sitemap is valid XML.
- Staging output is `noindex`.
- No provider identity leaks into the property unless explicitly attributed.
- Broken internal links fail the build.
- Generated site files are versioned by SiteConfig.

---

## 18. Milestone 14 — Seed validation cleanup

### Tasks

Use top-level Pydantic seed models.

Validate:

- duplicate IDs
- duplicate slugs
- empty seed queries
- malformed ZIP codes
- invalid coordinates
- unsupported country
- custom market with no members
- duplicate cities/ZIPs
- invalid provider categories
- invalid intent modifiers

Do not mutate parsed dictionaries using destructive operations.

### Acceptance criteria

- Errors include exact paths, for example:
  `services[3].seed_queries`.
- Seed loading is idempotent.
- Invalid input causes no partial database writes.
- Tests cover multiple invalid entries in one file.

---

## 19. Milestone 15 — CI and reproducibility

### Python

- Commit `uv.lock`.
- Use frozen installs.
- Run Ruff, mypy, and pytest.
- Pin supported Python version.

### Frontend

- Commit package lockfile.
- Use `npm ci`.
- Run type check and production build.

### Docker

- Run as non-root.
- Add health checks.
- Do not bake secrets into images.
- Parameterize environment variables:

```yaml
ALLOW_LIVE_API_CALLS: ${ALLOW_LIVE_API_CALLS:-false}
APP_DATA_MODE: ${APP_DATA_MODE:-fixture}
MAX_SCAN_COST_USD: ${MAX_SCAN_COST_USD:-10.00}
```

### GitHub Actions

Add:

- backend checks
- frontend checks
- Docker build
- fixture end-to-end test
- replay-mode contract test

Never run paid live tests on pull requests.

### Acceptance criteria

- Clean checkout builds reproducibly.
- CI passes with no API credentials.
- Docker containers run as non-root.
- Fixture and replay test suites make zero network calls.

---

## 20. Minimum test suite additions

Add tests for:

1. Fixture/live/replay separation
2. Replay normalization
3. Cache hit/miss/expiry
4. Dry run making zero requests
5. Cost threshold enforcement
6. Testing/full result separation
7. Scoring directionality
8. Missing-data confidence
9. Keyword variant grouping
10. Representative SERP selection
11. National versus local volume labels
12. ZIP/city/custom-market resolution
13. Partial DataForSEO responses
14. Billing failures
15. Rate limits
16. Schema drift
17. Failed scan lifecycle
18. Opportunity uniqueness
19. Asynchronous job locking
20. No site generation before approval
21. Valid sitemap
22. Staging `noindex`
23. Provider-independent output
24. Seed validation
25. Full fixture end-to-end workflow
26. Full replay end-to-end workflow

---

## 21. Recommended implementation order

Implement these work packages one at a time:

1. Baseline verification
2. Replay layer
3. Cache integration
4. Scan plan and cost enforcement
5. Testing/full score separation
6. Scoring rewrite
7. Keyword pipeline
8. Geographic resolution
9. Offline qualification harness
10. Lifecycle separation
11. Failure-state cleanup
12. Async jobs
13. Alembic and typed persistence
14. Site generator correctness
15. Seed validation
16. CI and reproducibility

Do not buy or use additional DataForSEO credits until work packages 1–9 are complete and passing offline tests.

---

## 22. Definition of done

This remediation is complete when:

1. Fixture and replay modes make zero network calls.
2. Previously captured DataForSEO responses can be replayed.
3. Repeated scans reuse cached results.
4. Dry runs and over-budget scans make zero paid calls.
5. Testing scans are never presented as full scores.
6. Scoring directionality is protected by invariant tests.
7. National demand is not mislabeled as city-level volume.
8. Locations resolve offline for supported U.S. cities and ZIPs.
9. The qualification harness validates all parser paths offline.
10. Discovery no longer generates sites or outreach automatically.
11. Failed scans do not become normal review opportunities.
12. Scans execute as persisted jobs.
13. Core data is stored in migrated typed tables.
14. CI passes without paid credentials.
15. No manually labeled training data is required.
16. The next live DataForSEO test can be limited to one request per capability and saved for future replay.

---

## 23. Explicit non-goals

Do not implement during this remediation:

- Google Ads API
- Google Places API
- Twilio
- automatic email sending
- provider billing
- production lead routing
- automated backlink campaigns
- machine-learning scoring
- bulk city-page generation
- automatic domain purchase
- automatic production deployment

The goal is to make the existing discovery engine trustworthy, inexpensive to iterate on, and safe to reconnect to DataForSEO later.
