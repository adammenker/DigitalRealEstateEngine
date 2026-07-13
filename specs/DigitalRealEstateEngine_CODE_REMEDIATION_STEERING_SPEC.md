# DigitalRealEstateEngine — Code Remediation Steering Specification

**Purpose:** Repair the current repository so it becomes a trustworthy, live-data V1 rather than a fixture-driven prototype.

**Scope:** Code correctness, architecture, persistence, integrations, testing, deployment safety, and developer tooling only.

**Out of scope:** New business-model features, production lead routing, provider billing, paid-ad automation, backlink automation, mass outreach, or ML trained on manually labeled opportunities.

---

## 1. Operating instructions for Codex

1. Work milestone by milestone in the order below.
2. Before editing, record the current branch and commit SHA.
3. Inspect the repository rather than assuming paths in this document are unchanged.
4. Keep the repository runnable after every milestone.
5. Run formatting, linting, type checking, unit tests, integration tests, and relevant build checks after each milestone.
6. Do not silently change scoring semantics or persisted schemas.
7. Add migrations for every database change.
8. Live API tests must be opt-in and cost bounded.
9. Fixture mode must remain supported for local development and CI.
10. Do not implement later milestones until the previous milestone’s acceptance criteria pass.
11. Document any deviation from this specification in `docs/implementation_deviations.md`.
12. Never present fixture or mock data as live data.

---

## 2. Required execution modes

Create an explicit application mode enum:

```text
fixture
live
```

Rules:

- `fixture` uses only deterministic fixtures and mock adapters.
- `live` uses configured production adapters.
- Live mode must fail fast when required credentials are absent.
- Never silently fall back from live mode to fixtures.
- Every page, API response, scan, score, and qualification report must expose its data mode.
- The UI must display a persistent and prominent “Synthetic fixture data” banner in fixture mode.
- Store `data_mode` and adapter versions on every `ScanRun`.

Acceptance criteria:

- Starting in live mode without DataForSEO credentials fails with an actionable error.
- A fixture scan is clearly labeled synthetic in the API and UI.
- Tests prove that no mock adapter is instantiated in live mode.

---

## 3. Milestone 0 — Establish a verified baseline

### Tasks

1. Run and document:
   - Python tests
   - Ruff
   - mypy
   - frontend tests if present
   - frontend type check
   - frontend production build
   - Docker build
2. Add missing commands to `pyproject.toml`, `package.json`, or a `Makefile`.
3. Add `docs/current_state.md` containing:
   - Commit SHA
   - Repository tree summary
   - Passing/failing checks
   - Existing live adapters
   - Existing mock adapters
   - Known broken paths
4. Do not “fix” tests by weakening assertions.

### Acceptance criteria

- One command runs the complete local verification suite.
- Baseline failures are documented before remediation starts.
- The repository can be installed from a clean checkout using documented commands.

---

## 4. Milestone 1 — Real API qualification framework

The qualification harness must test actual account capabilities rather than emit static pass results.

### Required qualification states

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

### Required capabilities

For DataForSEO:

1. Account/user-data lookup
2. Location lookup
3. Keyword discovery
4. Keyword metrics and monthly history
5. Localized organic SERP
6. SERP feature extraction
7. Business/provider listing retrieval
8. Competitor backlink/referring-domain metrics

For domain availability:

1. Availability lookup
2. Unknown/premium/reserved handling
3. Rate-limit and authentication handling

For deployment:

1. Credential validation
2. Static asset upload
3. Returned public staging URL
4. Staging smoke test

### Tasks

- Replace any hard-coded qualification report with executed adapter calls.
- Validate normalized output using Pydantic schemas.
- Persist qualification runs and individual capability results.
- Store sanitized raw responses for debugging and contract fixtures.
- Record estimated and actual API cost where available.
- Block live market scans until all required research capabilities pass.
- Make deployment and optional integrations non-blocking until their milestone.

### Acceptance criteria

- `qualify --fixtures` passes without network access.
- `qualify --live` performs real calls.
- A missing field produces `schema_mismatch`, not a false pass.
- A required capability failure prevents a live opportunity scan.
- Qualification output identifies exact endpoint, request ID, status, and missing fields.

---

## 5. Milestone 2 — Implement the live DataForSEO adapter

Keep the existing provider protocol, but implement a complete live adapter.

### Required methods

```python
resolve_location(...)
discover_keywords(...)
get_keyword_metrics(...)
get_serp_snapshot(...)
get_competitor_metrics(...)
find_providers(...)
```

### Implementation rules

- Keep DataForSEO response models inside the adapter package.
- Convert responses into provider-independent domain models.
- Support pagination and task polling where required.
- Preserve provider task IDs and raw payload references.
- Handle partial task failures.
- Use bounded retries only for safe transient failures.
- Enforce per-endpoint timeouts.
- Never map unavailable values to zero unless zero is the actual returned value.
- Include source timestamps and location granularity.
- Add recorded contract fixtures generated from sanitized live responses.
- Ensure fixture tests do not require paid calls.

### Acceptance criteria

- A live qualification run retrieves valid results for:
  - one city,
  - one ZIP resolved to a supported broader keyword market,
  - one custom multi-city market.
- Normalized models contain all fields required by scoring.
- A live scan no longer uses fixture-generated volumes, CPCs, providers, SERPs, or backlinks.
- Contract tests detect response-schema drift.

---

## 6. Milestone 3 — Correct the scoring system

The current score must be audited before it consumes live data.

### Replace ambiguous components

Use:

```text
Demand
Commercial value
Organic competitor weakness
Organic click availability
Provider suitability
Data confidence
```

### Directionality rules

- Stronger ranking competitors lower `Organic competitor weakness`.
- More relevant and locally targeted competitors lower `Organic competitor weakness`.
- More referring domains and stronger domains lower `Organic competitor weakness`.
- Ads, local packs, directories, and SERP features that displace organic results lower `Organic click availability`.
- A local pack must not create a positive organic-accessibility bonus.
- Insufficient and excessive provider supply may both be penalized.
- CPC is a commercial-value signal, not an organic-difficulty signal.

### Keyword handling

- Select representative SERPs using high-intent value, not original list order.
- Deduplicate exact and normalized duplicates.
- Add conservative grouping for close keyword variants.
- Do not blindly sum volumes for variants likely to represent the same demand.
- Record included and excluded keyword reasons.

### Missing data

- Each component has an explicit `available`, `partial`, or `missing` state.
- Missing data reduces confidence.
- Missing-data penalties scale with the number and importance of absent components.
- Do not give the maximum missing-data penalty for one minor missing field.
- Do not allow `high` confidence when a core component is absent.

### Configuration

Move all:

- weights,
- thresholds,
- caps,
- normalization constants,
- provider-count ranges,
- CPC ranges,
- authority thresholds

into versioned configuration.

### Acceptance criteria

- Unit tests assert the sign/direction of every scoring input.
- A stronger competitor fixture cannot improve the opportunity score.
- Adding a dominant local pack cannot improve organic click availability.
- Scores can be recalculated from stored raw/normalized data without new API calls.
- Every score stores formula version, component inputs, penalties, confidence, and explanation.

---

## 7. Milestone 4 — Location resolution and scan planning

### Required workflow

```text
Raw input
→ normalized service
→ location resolution
→ interpreted market preview
→ endpoint plan
→ cache inspection
→ uncached-cost estimate
→ confirmation
→ execution
```

### Tasks

- Call `resolve_location()` before creating a live scan.
- Preserve original user input and resolved provider identifiers.
- For ZIP input:
  - use coordinates/ZIP for SERP and provider research when supported;
  - resolve a supported city/county/metro for keyword metrics;
  - display the mismatch explicitly.
- Introduce a `ScanPlan` object listing every planned endpoint request.
- Estimate only uncached request cost.
- Reject plans exceeding `MAX_SCAN_COST_USD`.
- Add explicit user confirmation above a configurable threshold.
- Persist the plan with the scan run.

### Acceptance criteria

- Invalid or ambiguous locations produce actionable errors.
- ZIP resolution behavior is visible and tested.
- A scan over the maximum budget makes no paid calls.
- The UI displays expected calls, cache hits, and estimated cost before execution.

---

## 8. Milestone 5 — Wire caching into every paid request

### Tasks

- Compute deterministic cache keys from:
  - adapter,
  - endpoint,
  - API version,
  - normalized request parameters.
- Define TTL by endpoint.
- Preserve immutable historical SERP snapshots.
- Distinguish reusable cached data from intentionally refreshed snapshots.
- Store:
  - cache hit/miss,
  - source timestamp,
  - request cost,
  - raw response location.
- Add a force-refresh flag guarded by cost confirmation.

### Acceptance criteria

- Repeating an identical scan reuses eligible cached calls.
- Rescoring uses stored data and makes zero paid calls.
- Historical SERP snapshots are not overwritten.
- Cache TTL and force-refresh behavior have tests.

---

## 9. Milestone 6 — Persisted asynchronous scan jobs

Do not execute a live scan inside a single HTTP request.

### Required endpoints

```text
POST /scans
GET /scans/{scan_run_id}
POST /scans/{scan_run_id}/cancel
POST /scans/{scan_run_id}/retry
```

### Required scan stages

```text
queued
resolving_location
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

- Persist jobs before execution.
- Use an in-process database-backed worker for V1.
- Persist progress, costs, errors, and partial results.
- Support browser refresh without losing status.
- Make retry stage-aware and idempotent.
- Ensure two workers cannot process the same job concurrently.
- A failed scan must not become `review_required`.

### Acceptance criteria

- Scan creation returns immediately with a run ID.
- Progress is queryable.
- Worker restart does not corrupt job state.
- Partial and failed states are distinguishable.
- Cancellation prevents subsequent paid stages when possible.

---

## 10. Milestone 7 — Database migrations and typed persistence

### Tasks

- Add an Alembic baseline migration.
- Remove normal-startup reliance on `Base.metadata.create_all()`.
- Add typed tables for:
  - keyword metrics,
  - keyword inclusion decisions,
  - SERP snapshots,
  - SERP results,
  - competitor metrics,
  - provider candidates,
  - score snapshots,
  - domain candidates,
  - outreach drafts,
  - site configurations,
  - assets,
  - deployments,
  - qualification results,
  - manual interventions.
- Keep raw JSON payload references in addition to typed rows.
- Add appropriate:
  - foreign keys,
  - unique constraints,
  - indexes,
  - timestamps,
  - enum/check constraints.
- Add a uniqueness rule for a canonical `service family × market` opportunity.
- Add migration tests against a populated pre-migration database fixture.

### Acceptance criteria

- `alembic upgrade head` creates the complete schema.
- Existing data survives upgrades.
- Normal app startup never alters the schema automatically.
- Querying opportunities does not require parsing generic JSON blobs for core fields.

---

## 11. Milestone 8 — Correct lifecycle and site-generation behavior

### Lifecycle

```text
scan
→ review
→ approve
→ choose/edit domain candidate
→ edit SiteConfig
→ generate preview
→ deploy staging
```

### Tasks

- Stop generating a site during scanning.
- Require explicit approval before SiteConfig creation.
- Version SiteConfig and generated builds.
- Make builds idempotent for the same config version.
- Write generated files only after validation succeeds.
- Keep property identity separate from provider routing configuration.

### Site correctness fixes

- Generate valid sitemap XML.
- Add configurable base URL.
- Add canonical links.
- Add meta descriptions and Open Graph metadata.
- Add `noindex` to staging.
- Add optimized image handling and asset provenance.
- Render only approved content/assets.
- Ensure generic content is not duplicated.
- Make the contact form explicitly disabled in staging or connect it to a staging-safe handler.
- Add HTML validation and internal-link checks.
- Do not use false `LocalBusiness` structured data.
- Include a clear referral disclosure.
- Add tests proving no provider identity is embedded in the property unless explicitly attributed.

### Acceptance criteria

- Scans produce no generated-site files.
- An approved opportunity can create a validated SiteConfig.
- The generated site passes sitemap, canonical, internal-link, disclosure, and provider-independence tests.
- Staging pages are `noindex`.

---

## 12. Milestone 9 — Real domain availability adapter

### Tasks

- Implement the configured domain-availability provider.
- Retain the mock adapter for fixtures.
- Model:
  - available,
  - unavailable,
  - unknown,
  - premium/reserved.
- Never claim final purchase price unless returned reliably.
- Mark availability timestamps and provider.
- Add rate-limit, billing, and malformed-domain handling.
- Prevent a stale domain check from being presented as current without a warning.

### Acceptance criteria

- Live checks return normalized statuses.
- Fixture mode remains deterministic.
- UI clearly separates “likely available” from “purchased.”
- No automatic purchasing occurs.

---

## 13. Milestone 10 — Deployment provider abstraction and real staging

Keep:

```python
class DeploymentProvider(Protocol):
    async def deploy_staging(...)
```

Implement exactly one real staging adapter initially.

Supported choices:

- Cloudflare Pages Direct Upload
- AWS Amplify Hosting

Do not implement both during remediation.

### Common requirements

- Upload prebuilt static assets.
- Return a public HTTPS staging URL.
- Persist deployment metadata.
- Run an HTTP smoke test.
- Require explicit confirmation.
- Mark staging as `noindex`.
- Never attach a production custom domain automatically.
- Never return a local `file://` path as a successful cloud deployment.

### Acceptance criteria

- One generated fixture property deploys to a public staging URL.
- Smoke test checks HTTP 200, title, referral disclosure, and `noindex`.
- Failed upload creates a failed Deployment record with actionable diagnostics.

---

## 14. Milestone 11 — Outreach and provider workflow cleanup

### Tasks

- Remove hard-coded sender names.
- Move sender identity into settings.
- Add templates for:
  - initial pilot email,
  - follow-up email,
  - call opening,
  - voicemail,
  - proven-results offer.
- Store template version and structured facts used.
- Never generate unsupported performance claims.
- Add editable drafts and manual sent timestamps.
- Add provider contact status and follow-up tracking.
- Keep sending manual; do not integrate an email sender.

### Acceptance criteria

- Draft output uses only stored structured facts.
- Sender identity is configurable.
- Proven-results language cannot be generated without actual stored metrics.
- No message is sent by the application.

---

## 15. Milestone 12 — SERP classification hardening

### Required classifications

```text
local_provider
directory
national_brand
government_or_nonprofit
informational_publisher
marketplace
lead_generator
unknown
```

### Tasks

- Move known-domain lists into versioned configuration.
- Cross-reference provider/business-listing matches.
- Store classification confidence and rule/version used.
- Resolve overlapping rule priority explicitly.
- Allow user overrides without mutating original classification.
- Do not require overrides for the scanner to work.
- Optional LLM classification may be added only as a non-blocking fallback.

### Acceptance criteria

- Unit tests cover representative domains and ambiguous cases.
- An override is auditable and reversible.
- Classifier version is stored with every result.
- Unknown results remain unknown rather than being forced into a favorable class.

---

## 16. Milestone 13 — Seed validation

### Tasks

Use top-level Pydantic models for service and location seed files.

Validate:

- duplicate IDs,
- duplicate slugs,
- empty query sets,
- malformed ZIP/postal codes,
- invalid coordinates,
- unsupported country handling,
- custom market with no cities/ZIPs,
- overlapping duplicate market members,
- malformed provider categories.

Do not mutate parsed source dictionaries with destructive operations.

### Acceptance criteria

- Errors include exact paths such as:
  - `services[4].seed_queries`
  - `locations[2].postal_codes[1]`
- Seed loading is idempotent.
- Invalid entries do not partially corrupt the database.

---

## 17. Milestone 14 — CI, reproducibility, and container security

### Python

- Commit `uv.lock`.
- Use frozen installs in CI and Docker.
- Run Ruff, mypy, and pytest.
- Pin supported Python version.

### Frontend

- Commit a package lockfile.
- Use `npm ci`.
- Run type checking and production build.
- Use a minimal/standalone runtime image when supported.

### Docker

- Run services as non-root users.
- Do not bake secrets into images.
- Parameterize environment values:

```yaml
ALLOW_LIVE_API_CALLS: ${ALLOW_LIVE_API_CALLS:-false}
MAX_SCAN_COST_USD: ${MAX_SCAN_COST_USD:-10.00}
```

- Add health checks.
- Use configured persistent volumes for database, cache, and generated output.

### GitHub Actions

Add workflows for:

- backend checks,
- frontend checks,
- Docker build,
- fixture end-to-end test.

Live contract tests must never run automatically on untrusted pull requests.

### Acceptance criteria

- Clean checkout builds reproducibly.
- CI passes with no paid credentials.
- Containers run as non-root.
- `.env` values can activate live mode without editing Compose files.

---

## 18. Milestone 15 — Security before public app deployment

This applies to the internal scanner application, not only generated public sites.

### Tasks

- Add single-user authentication before exposing the scanner publicly.
- Authorize paid scan and deployment actions server-side.
- Configure allowed origins from settings.
- Add CSRF protection where applicable.
- Rate-limit state-changing endpoints.
- Redact credentials and unnecessary contact data from logs.
- Add pagination to growing result lists.
- Validate and constrain filesystem output paths.

### Acceptance criteria

- An unauthenticated user cannot trigger scans or deployments.
- API keys are never exposed to the browser.
- Public generated sites do not expose internal opportunity/provider records.

---

## 19. Minimum required tests

Add or strengthen tests for:

1. Fixture/live adapter separation
2. Live-mode missing credentials
3. Qualification state handling
4. DataForSEO normalization
5. Provider schema drift
6. Scoring directionality
7. Missing-data confidence
8. Keyword deduplication
9. Representative SERP selection
10. Location resolution
11. Scan cost enforcement
12. Cache TTL and historical snapshots
13. Async job locking and retry
14. Database migrations
15. Opportunity uniqueness
16. Failed/partial scan lifecycle
17. Domain availability normalization
18. Seed validation
19. SERP classification
20. Site provider independence
21. Valid sitemap XML
22. Canonical and `noindex` behavior
23. Public staging smoke test
24. Outreach claim safety
25. Docker and frontend production builds

---

## 20. Completion definition

Remediation is complete only when:

1. The app can run fully in deterministic fixture mode.
2. Live qualification verifies all required DataForSEO capabilities.
3. Live scans use no synthetic research data.
4. Scoring directionality is covered by tests.
5. Scan costs are planned, cached, bounded, and persisted.
6. Scans run as persisted asynchronous jobs.
7. Core data is stored in typed migrated tables.
8. Site generation occurs only after approval.
9. Generated staging sites are valid, provider-independent, and `noindex`.
10. Domain availability is live or clearly marked unknown.
11. One real deployment provider returns a public staging URL.
12. CI and clean builds pass without paid credentials.
13. No manually labeled training data is needed.
14. Documentation clearly separates fixture, live, staging, and future production behavior.

---

## 21. Explicit non-goals during remediation

Do not add:

- Google Ads API
- Google Places API as the persistent provider database
- automated email sending
- automated calls or texts
- Twilio lead routing
- provider billing
- production domains
- backlink automation
- ML-based scoring
- bulk city-page publishing
- automatic site deployment after scanning

Finish and verify the live V1 foundation first.
