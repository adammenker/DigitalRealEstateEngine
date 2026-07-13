# Digital Realestate Engine — Initial Implementation Specification

**Version:** 0.1  
**Audience:** Codex / coding agent  
**Primary language:** Python  
**Goal:** Build a local-first platform that discovers promising `service family × local market` opportunities, explains why they may be attractive, generates domain suggestions and provider outreach drafts, and produces one provider-independent sample site that can be previewed locally and deployed to staging.

---

## 1. Product objective

Build an internal research and site-generation platform for rank-and-rent local lead generation.

The platform must:

1. Read service families from a seed file.
2. Read locations from a separate seed file.
3. Support one-off scans from the UI using a service plus city, ZIP code, or other resolvable location.
4. Retrieve enough external data to evaluate demand, commercial intent, organic competition, SERP obstacles, and provider supply.
5. Produce deterministic, explainable opportunity scores.
6. Require no manually labeled training data.
7. Generate several sensible domain-name candidates and check likely availability.
8. Identify potential service providers and generate manually sendable outreach copy.
9. Store site configuration separately from provider configuration.
10. Generate one provider-independent sample site from structured data.
11. Preview the site locally and deploy it to a staging URL.
12. Cache raw API responses and make all external integrations replaceable.

The system must not claim that an opportunity is guaranteed to rank or be profitable.

---

## 2. Core business model represented in the software

A **digital property** is:

> One service family serving one coherent local market.

Examples:

- Water heater services in lower Fairfield County, Connecticut
- Epoxy flooring in the Jersey City/Hoboken market
- Tree removal in a defined group of adjacent suburbs

A property is independent of any contractor or service provider.

The property owns:

- Domain and brand
- Public website
- SEO content
- Public contact forms
- Future tracking number
- Analytics
- Lead history
- Rankings and search-performance history

A provider is only a replaceable lead destination.

Changing providers must not require changing the property brand, pages, domain, public phone number, forms, or analytics.

---

## 3. V1 scope

V1 includes:

- API qualification harness
- Service and location seed ingestion
- One-off UI scans
- Location resolution
- Keyword-cluster discovery
- Search-volume, CPC, seasonality, SERP, backlink, Maps/business-listing retrieval
- Explainable opportunity scoring
- Ranked opportunity dashboard
- Raw-response caching
- Cost estimation and scan limits
- Domain-name generation and likely-availability checks
- Provider discovery
- Outreach email, call, and voicemail draft generation
- Manual provider-contact tracking
- Structured site configuration
- Provider-independent static site generation
- Local site preview
- One staging deployment target
- Mock fixtures and end-to-end tests

V1 does not include:

- Automatic domain purchasing
- Automatic production deployment
- Automatic email sending
- Mass outreach
- Automatic Google Business Profile creation
- Paid-ad campaign automation
- Lead routing
- Call tracking
- Billing providers monthly
- Automated backlink outreach
- ML models requiring labeled examples
- Hundreds of automatically published city pages

---

## 4. V2 architectural preparation

V1 must create interfaces and data models that allow V2 to add:

- Domain registration and DNS configuration
- Production deployment
- Search Console integration
- GA4 integration
- Forms and lead storage
- Twilio-based tracking numbers and call forwarding
- Provider routing configuration
- Provider switching
- Lead qualification and booked-job outcomes
- Portfolio monitoring
- Rent and billing records
- SEO-performance feedback into scoring

Do not implement these production features in V1 unless required for the single staging demo.

---

## 5. Required technology stack

Use a single Python repository.

Recommended stack:

- Python 3.12+
- FastAPI
- Jinja2 templates
- HTMX for lightweight interactivity
- SQLAlchemy 2.x
- Alembic
- SQLite by default
- PostgreSQL-compatible schema through `DATABASE_URL`
- Pydantic v2
- Typer for CLI commands
- `httpx` for HTTP clients
- `tenacity` for retry policies
- `pytest`
- `respx` or equivalent for HTTP mocking
- Ruff
- mypy
- structured logging

Avoid a separate React application in V1 unless there is a clear implementation need.

---

## 6. Suggested repository layout

```text
rank-rent/
├── README.md
├── pyproject.toml
├── .env.example
├── alembic.ini
├── migrations/
├── config/
│   ├── scoring.yaml
│   ├── app.yaml
│   └── outreach_templates/
├── seeds/
│   ├── services.example.yaml
│   └── locations.example.yaml
├── fixtures/
│   ├── dataforseo/
│   ├── domains/
│   ├── images/
│   └── expected/
├── src/rank_rent/
│   ├── main.py
│   ├── cli.py
│   ├── settings.py
│   ├── db/
│   ├── models/
│   ├── repositories/
│   ├── domain/
│   ├── scoring/
│   ├── integrations/
│   │   ├── base.py
│   │   ├── dataforseo/
│   │   ├── domain_availability/
│   │   ├── image_search/
│   │   ├── contact_discovery/
│   │   ├── content_generation/
│   │   └── deployment/
│   ├── services/
│   ├── site_generator/
│   ├── web/
│   │   ├── routes/
│   │   ├── templates/
│   │   └── static/
│   └── qualification/
├── generated_sites/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── e2e/
└── scripts/
```

---

## 7. External integrations

### 7.1 Required V1 integration: DataForSEO

Use DataForSEO as the primary research provider.

The adapter must support:

- Location resolution
- Keyword ideas and related keywords
- Keyword metrics
- Monthly search trends
- CPC and paid-competition fields
- Localized organic SERP retrieval
- SERP feature detection
- Maps/local results or business listings
- Backlink and referring-domain metrics for ranking competitors

Do not couple domain logic to DataForSEO response shapes.

Create normalized provider-independent models.

Required interface:

```python
class MarketResearchProvider(Protocol):
    async def resolve_location(self, query: str) -> ResolvedLocation: ...
    async def discover_keywords(
        self,
        service: ServiceFamily,
        market: Market,
    ) -> list[KeywordCandidate]: ...
    async def get_keyword_metrics(
        self,
        keywords: list[str],
        market: Market,
    ) -> list[KeywordMetric]: ...
    async def get_serp_snapshot(
        self,
        keyword: str,
        market: Market,
    ) -> SerpSnapshot: ...
    async def get_competitor_metrics(
        self,
        urls: list[str],
    ) -> list[CompetitorMetric]: ...
    async def find_providers(
        self,
        service: ServiceFamily,
        market: Market,
    ) -> list[ProviderCandidate]: ...
```

### 7.2 Domain availability

Default adapter: WhoisXML Domain Availability API or another provider with equivalent behavior.

Required interface:

```python
class DomainAvailabilityProvider(Protocol):
    async def check(self, domain: str) -> DomainAvailabilityResult: ...
```

The result must support:

- `available`
- `unavailable`
- `unknown`
- `premium_or_reserved`
- provider raw status
- checked timestamp

Final purchase price is not required in V1.

### 7.3 Optional image search

Default adapter: Pexels.

Store:

- Source URL
- Photographer
- Provider
- Attribution URL
- License/source metadata
- Downloaded asset path
- Alt text
- Search query used

The system must also support manual image upload.

### 7.4 Optional contact discovery

Default adapter: Hunter or equivalent.

This adapter is optional and must not block provider discovery.

Support:

- Found email
- Confidence
- Verification status
- Source
- Generic vs personal address
- Website contact-form URL
- Phone number
- Manual override

### 7.5 Optional content generation

Support two implementations:

1. Deterministic template generator
2. Optional LLM-backed generator

The deterministic implementation is mandatory.

The LLM implementation must accept structured input and return schema-validated output. It must never be required for scoring or core operation.

### 7.6 Staging deployment

Default staging adapter: Cloudflare Pages.

Required interface:

```python
class DeploymentProvider(Protocol):
    async def deploy_staging(
        self,
        build_directory: Path,
        project_slug: str,
    ) -> DeploymentResult: ...
```

A local preview must work with no cloud credentials.

---

## 8. Environment variables

Provide `.env.example` with at least:

```text
APP_ENV=development
DATABASE_URL=sqlite:///./rank_rent.db

DATAFORSEO_LOGIN=
DATAFORSEO_PASSWORD=

WHOISXML_API_KEY=
PEXELS_API_KEY=
HUNTER_API_KEY=
OPENAI_API_KEY=

CLOUDFLARE_API_TOKEN=
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_PAGES_PROJECT=

MAX_SCAN_COST_USD=10.00
ALLOW_LIVE_API_CALLS=false
```

No secrets may be committed.

---

## 9. Seed-file formats

### 9.1 Service seeds

Use YAML.

```yaml
services:
  - id: water_heater_services
    display_name: Water Heater Services
    description: Repair, replacement, installation, and emergency service
    seed_queries:
      - water heater repair
      - water heater replacement
      - emergency water heater repair
      - tankless water heater installation
    negative_terms:
      - diy
      - jobs
      - salary
      - parts
      - manual
    provider_categories:
      - plumber
      - water heater installation service
    regulated: false
    enabled: true
```

Rules:

- `seed_queries` are starting points, not manually labeled examples.
- `negative_terms` filter obvious informational or employment intent.
- Missing optional fields must not prevent scanning.
- Invalid entries must produce line-level validation errors.

### 9.2 Location seeds

Use YAML.

```yaml
locations:
  - id: lower_fairfield_county
    display_name: Lower Fairfield County, CT
    type: market
    country_code: US
    state: CT
    cities:
      - Stamford
      - Greenwich
      - Norwalk
    postal_codes:
      - "06901"
      - "06830"
      - "06850"
    center:
      latitude: 41.0534
      longitude: -73.5387
    enabled: true
```

Support location types:

- city
- ZIP/postal code
- county
- metro
- custom market composed of adjacent cities/ZIPs

The engine must preserve the user’s original input and the normalized provider location identifier.

---

## 10. One-off scan UI

The dashboard must include a form with:

- Service text
- Location text
- Optional country
- Optional advanced controls
- Estimated scan cost
- Dry-run button
- Run-scan button

Examples:

- `water heater repair` + `Stamford, CT`
- `epoxy flooring` + `07302`
- `tree removal` + `Westchester County, NY`

The engine must:

1. Resolve the location.
2. Show the interpreted market before expensive calls.
3. Allow the user to cancel or proceed.
4. Save the ad hoc service and market as reusable records.
5. Never require editing seed files for a one-off scan.

For ZIP input:

- Use the ZIP or coordinates for localized SERP/provider analysis when supported.
- Use the nearest supported city/county/metro for keyword-volume data.
- Show this resolution explicitly in the UI.

---

## 11. Core data model

Implement at least the following entities.

### 11.1 ServiceFamily

- `id`
- `slug`
- `display_name`
- `description`
- `seed_queries`
- `negative_terms`
- `provider_categories`
- `regulated`
- `enabled`
- timestamps

### 11.2 Market

- `id`
- `slug`
- `display_name`
- `type`
- `country_code`
- `state`
- `cities`
- `postal_codes`
- `latitude`
- `longitude`
- `provider_location_code`
- `provider_location_name`
- `resolution_metadata`
- timestamps

### 11.3 Opportunity

- `id`
- `service_family_id`
- `market_id`
- `status`
- `latest_score`
- `score_version`
- `confidence`
- `missing_data_flags`
- `approved_at`
- timestamps

Suggested statuses:

```text
discovered
scanning
review_required
approved
rejected
provider_outreach
site_preview
staging
live
pilot
rented
paused
```

### 11.4 ScanRun

- `id`
- `source` (`seed`, `manual`, `rescan`)
- `status`
- `estimated_cost_usd`
- `actual_cost_usd`
- `started_at`
- `completed_at`
- `error_summary`
- `integration_versions`
- `request_parameters`

### 11.5 KeywordCluster and KeywordMetric

Store:

- Keyword
- Canonicalized keyword
- Intent
- Search volume
- CPC
- Paid competition
- Monthly history
- Source
- Source timestamp
- Market granularity
- Included/excluded reason

### 11.6 SerpSnapshot and SerpResult

Store:

- Query
- Market
- Device
- Captured timestamp
- Result order
- Result type
- URL
- Domain
- Title
- Description
- Is local provider
- Is directory
- Is national brand
- Is lead-generation site
- SERP features present
- Raw-response reference

### 11.7 CompetitorMetric

Store:

- URL
- Domain
- Referring domains
- Backlinks
- Authority/rank values returned by provider
- Page relevance score
- Local relevance
- Page type
- Captured timestamp

### 11.8 ProviderCandidate

Store:

- Name
- Website
- Phone
- Email
- Contact form URL
- Address
- Service area
- Category
- Rating
- Review count
- Business status
- Contact confidence
- Source
- Raw-response reference
- Outreach status

### 11.9 OpportunityScore

Store:

- Total score
- Component scores
- Input measurements
- Missing-data penalties
- Scoring version
- Human-readable explanation
- Timestamp

### 11.10 DomainCandidate

Store:

- Domain
- Pattern used
- Availability status
- Readability score
- Relevance score
- Brandability score
- Expansion score
- Risk flags
- Rank
- Checked timestamp

### 11.11 OutreachDraft

Store:

- Provider candidate
- Opportunity
- Type (`email`, `call`, `voicemail`, `follow_up`)
- Template version
- Generated body
- Subject
- Facts used
- Generation method
- Manually edited body
- Status
- Sent timestamp entered manually

### 11.12 SiteConfig

Keep property data separate from provider data.

Property fields:

- Property brand
- Domain candidate
- Service family
- Market
- Service-area display text
- Contact disclosure
- Public email placeholder
- Public phone placeholder
- Services
- FAQs
- Pricing guidance
- Images
- Metadata
- Legal/disclosure content

Provider routing fields must be in a separate provider configuration record.

### 11.13 Asset

- Type
- Local path
- Source provider
- Source URL
- Attribution
- License metadata
- Alt text
- Approved status

### 11.14 Deployment

- Site config
- Environment
- Provider
- URL
- Commit/build identifier
- Status
- Created timestamp
- Error details

---

## 12. Raw API response storage and caching

Every paid API call must:

1. Compute a deterministic cache key from provider, endpoint, normalized parameters, and API version.
2. Check cache before making a live request.
3. Store raw JSON separately from normalized database rows.
4. Record request time, response time, status, cost if available, and provider task ID.
5. Support configurable TTL by endpoint.
6. Allow rescoring from stored data without calling the provider again.
7. Never silently replace historical snapshots.

Use compressed files or a database JSON column, but preserve exact source payloads.

---

## 13. API qualification harness

This is Milestone 0 and must be completed before building the full scanner.

Create CLI commands:

```bash
rank-rent qualify --fixtures
rank-rent qualify --live
rank-rent qualify --live --service water_heater_services --locations lower_fairfield_county
```

The live qualification run must:

1. Load one service family.
2. Load at least three different location inputs:
   - city
   - ZIP code
   - custom market
3. Resolve each location.
4. Discover keywords.
5. Retrieve keyword metrics.
6. Retrieve at least one localized SERP per market.
7. Retrieve competitor metrics for top organic results.
8. Retrieve provider candidates.
9. Generate domain candidates.
10. Check likely domain availability.
11. Retrieve one optional image if configured.
12. Generate one outreach draft using deterministic templates.
13. Generate one site.
14. Preview or build the site.
15. Optionally deploy to staging.
16. Produce a capability report.

Capability report fields:

- Integration
- Endpoint/capability
- Passed/failed
- Required fields found
- Missing fields
- Sample counts
- Estimated and actual cost
- Storage restrictions noted in code comments/docs
- Manual intervention required
- Blocking/non-blocking classification

The program must stop subsequent paid milestones if a blocking required capability fails.

---

## 14. Opportunity-discovery workflow

For each enabled `service family × market`:

1. Create or load an Opportunity.
2. Expand seed queries into a keyword cluster.
3. Remove duplicates and negative-intent terms.
4. Retrieve keyword metrics.
5. Select representative high-intent queries.
6. Retrieve localized SERPs.
7. Classify result types.
8. Retrieve competitor metrics for top organic results.
9. Retrieve provider candidates.
10. Compute component scores.
11. Apply missing-data penalties.
12. Save the full score explanation.
13. Mark the opportunity `review_required`.

The engine must never publish a site automatically after scanning.

---

## 15. Scoring model

Use deterministic, versioned scoring.

Initial total: 100 points.

Suggested components:

- Demand: 25
- Commercial intent: 15
- Organic accessibility: 30
- SERP accessibility: 15
- Provider supply: 15
- Missing-data penalty: up to -30

All weights must live in `config/scoring.yaml`.

### 15.1 Demand

Possible inputs:

- Deduplicated cluster search volume
- Number of high-intent keywords
- Seasonality concentration
- Market-granularity confidence

Do not sum near-duplicate keyword volumes blindly.

### 15.2 Commercial intent

Possible inputs:

- CPC percentile
- Paid-ad presence
- Transactional modifiers
- Emergency/replacement/installation intent
- Informational-intent ratio

CPC is not organic difficulty.

### 15.3 Organic accessibility

Possible inputs:

- Referring domains of ranking pages
- Domain strength of ranking domains
- Exact query relevance of current pages
- Number of weak or poorly targeted results
- Local-business result share
- Directory and national-brand share

Do not use one vendor’s keyword-difficulty number as the sole signal.

### 15.4 SERP accessibility

Possible inputs:

- Position and count of ads
- Maps/local-pack presence
- Directory dominance
- Organic-result visibility
- Special-result features that reduce organic clicks

### 15.5 Provider supply

Possible inputs:

- Number of credible providers
- Number with websites
- Number with reachable contact channels
- Rating/review distribution
- Geographic coverage

Too few providers and extreme provider saturation should both reduce the score.

### 15.6 Confidence and missing data

Each opportunity must display:

- Score
- Confidence (`high`, `medium`, `low`)
- Missing fields
- Assumptions
- Data timestamps
- Why the score changed from the previous scan

No missing value may be silently converted to zero unless zero is semantically correct.

---

## 16. SERP-result classification

Implement deterministic classification rules with editable domain lists and heuristics.

Categories:

- Local provider
- Directory
- National brand
- Government/nonprofit
- Informational publisher
- Marketplace
- Lead-generation/referral site
- Unknown

Use:

- Known-domain lists
- Business listing matches
- URL patterns
- Page title/snippet
- Domain characteristics

The classification may use an optional LLM only as a fallback. The default path must remain deterministic and testable.

Allow the user to correct a classification, but the scanner must work without corrections.

Corrections are overrides, not training labels.

---

## 17. Dashboard requirements

### 17.1 Opportunity list

Show:

- Service family
- Market
- Total score
- Confidence
- Demand
- Commercial intent
- Organic accessibility
- SERP accessibility
- Provider supply
- Estimated scan cost
- Last scanned
- Status

Support sorting and filtering.

### 17.2 Opportunity detail

Show:

- Executive explanation
- Positive signals
- Risks
- Missing data
- Keyword cluster
- Monthly demand trend
- SERP composition
- Top competitors
- Provider candidates
- Domain candidates
- Scan history
- Raw-source links/internal IDs
- Manual notes
- Approve/reject controls

### 17.3 Scan management

Show:

- Pending scans
- Running scans
- Failures
- API cost
- Cache hits
- Retry action
- Qualification status

---

## 18. Domain-name recommendation module

Generate 5–10 candidates for approved opportunities.

Candidate patterns may include:

```text
{city}{service}help.com
{region}{service}.com
{service}pros{state}.com
{city}{service}guide.com
{brand_word}{service}.com
{region}homehelp.com
```

Requirements:

- Normalize service and market terms.
- Prefer `.com`.
- Avoid hyphens by default.
- Avoid repeated letters and awkward word boundaries.
- Penalize length.
- Penalize names tied to a provider.
- Penalize misleading claims such as `best`, `official`, or guaranteed language.
- Reward readability.
- Reward relevance.
- Reward room to expand within the same service family or market.
- Check likely availability when an API key is configured.
- Mark results `unknown` when availability cannot be confirmed.
- Do not purchase domains.
- Do not represent the check as trademark clearance.

---

## 19. Provider independence

The generated property must not:

- Use a provider’s business name as the property brand
- Use the provider’s logo as the primary property identity
- Claim that the property itself performs services
- Invent an address
- Invent employees
- Invent reviews
- Invent credentials
- Invent licenses
- Invent local photographs
- Create a fake Google Business Profile

Site copy must disclose that inquiries may be connected with an independent local service provider.

Provider-specific fields must be stored separately and injected only into routing or clearly attributed sections.

---

## 20. Provider discovery and outreach

### 20.1 Provider discovery

For an approved opportunity, rank potential providers using:

- Service/category match
- Market coverage
- Active business status
- Website availability
- Contactability
- Review count
- Rating
- Business size proxies
- Obvious disqualifiers

Do not assume the highest-rated provider is the best pilot.

### 20.2 Outreach generation

Generate:

- Initial pilot email
- Follow-up email
- Call opening
- Voicemail
- Proven-results offer template

The initial pilot message should offer early leads without falsely claiming existing volume.

The proven-results message may include metrics only when those metrics exist in stored property data.

Use only verified structured facts.

Outreach must be manually reviewed and manually sent.

No email-sending API is required in V1.

### 20.3 Outreach tracking

Allow the user to record:

- Contacted date
- Channel
- Draft used
- Response
- Follow-up date
- Interested/not interested
- Pilot status
- Notes

---

## 21. Site-generation system

Generate one provider-independent static sample site from `SiteConfig`.

### 21.1 Required pages

- Home
- Service-family overview
- Individual service pages
- Service-area page
- Pricing/cost guidance
- FAQ
- About/referral disclosure
- Contact page
- Privacy page
- Terms/disclaimer page

Do not generate dozens of city pages in V1.

### 21.2 Required structured inputs

- Brand
- Market
- Service family
- Included services
- Service area
- FAQ entries
- Pricing guidance
- Images
- Contact placeholders
- Disclosure
- Metadata
- Schema markup configuration

### 21.3 Content rules

- No fabricated claims.
- No fake testimonials.
- No fake years in business.
- No claim of being the service provider.
- Avoid generic keyword stuffing.
- Avoid city-name substitution across thin pages.
- Generated text must be editable before deployment.
- All content sections must record generation source and approval status.

### 21.4 Technical site requirements

- Static HTML output
- Mobile responsive
- Accessible forms and navigation
- Semantic headings
- Canonical URLs
- Sitemap
- Robots file
- Open Graph metadata
- JSON-LD appropriate for a referral/informational site
- No LocalBusiness schema that falsely represents the site as a contractor
- Fast loading
- Optimized images
- Internal links
- Valid HTML
- No runtime dependency required for basic pages

### 21.5 Local preview

Commands:

```bash
rank-rent site generate <opportunity_id>
rank-rent site preview <opportunity_id>
```

The generated site must be written to:

```text
generated_sites/<property_slug>/
```

### 21.6 Staging deployment

Command:

```bash
rank-rent site deploy-staging <opportunity_id>
```

Requirements:

- Build locally first.
- Require explicit confirmation.
- Deploy only one configured sample/staging property in V1.
- Return and store the staging URL.
- Never attach a purchased domain automatically.
- Never deploy to production automatically.

---

## 22. Cost controls

Before any live scan:

- Estimate cost by endpoint and request count.
- Show the estimate in CLI/UI.
- Require confirmation when above a configurable threshold.
- Enforce `MAX_SCAN_COST_USD`.
- Support dry-run mode.
- Use cache before live calls.
- Limit keyword and competitor counts.
- Rate-limit requests.
- Retry only safe transient failures.
- Record actual cost when the provider exposes it.

A failed scan must not enter an uncontrolled retry loop.

---

## 23. Error handling

Classify errors:

- Authentication
- Authorization
- Quota
- Billing
- Invalid location
- No data
- Provider timeout
- Rate limit
- Schema drift
- Partial response
- Internal normalization error

Requirements:

- Preserve partial results.
- Mark incomplete capabilities.
- Show actionable error messages.
- Never silently score a failed component as zero.
- Save raw failed responses when safe.
- Include provider request/task IDs in logs.

---

## 24. Testing strategy

### 24.1 Unit tests

Cover:

- Seed validation
- Location normalization
- Keyword deduplication
- Negative-term filtering
- SERP classification
- Score calculations
- Missing-data penalties
- Domain generation
- Domain ranking
- Outreach template rendering
- Site-config validation
- Provider separation

### 24.2 Integration tests

Use mocked HTTP responses for every adapter.

Cover:

- Authentication headers
- Request serialization
- Response normalization
- Pagination
- Retry behavior
- Rate limits
- Partial data
- Provider schema changes

### 24.3 Contract tests

When live credentials are enabled, run a small read-only suite against each provider.

Contract tests must be opt-in and cost bounded.

### 24.4 End-to-end fixture test

Required path:

```text
service seed
→ location seed
→ opportunity creation
→ mocked research data
→ score
→ approval
→ domain suggestions
→ provider candidates
→ outreach draft
→ site config
→ static site build
→ local preview artifact
```

### 24.5 Staging smoke test

With cloud credentials:

```text
fixture opportunity
→ generated site
→ staging deployment
→ HTTP 200
→ expected title
→ expected disclosure
→ no provider-specific identity
```

---

## 25. Manual-effort audit

Create an `InterventionLog` entity.

Whenever the user performs a manual action, record:

- Opportunity
- Lifecycle stage
- Action type
- Start/end timestamps or estimated duration
- Reason automation could not complete it
- Whether it is expected to recur for every property
- Suggested future automation

Initial manual-action categories:

- Opportunity review
- Content review
- Domain purchase
- Missing contact lookup
- Outreach send
- Provider selection
- Provider follow-up
- Link acquisition
- Pilot outcome collection

Add a dashboard report showing estimated manual minutes per property.

This is required so scaling bottlenecks are measured rather than guessed.

---

## 26. Observability

Implement:

- Structured logs
- Scan-run IDs
- Integration request IDs
- Cost counters
- Cache-hit counters
- Error counters
- Duration metrics
- Qualification status
- Deployment status

Do not log secrets or full sensitive contact data unnecessarily.

---

## 27. Security and compliance defaults

- No secrets in repository.
- Validate all user input.
- Escape generated content.
- Restrict file writes to configured directories.
- Require confirmation before live API calls above cost threshold.
- Require confirmation before staging deployment.
- No automatic email sending.
- No automatic calls or texts.
- No call recording.
- No fake business listings.
- Preserve asset attribution.
- Add clear referral disclosure to generated sites.

---

## 28. Implementation milestones

### Milestone 0 — API qualification

Deliver:

- Integration interfaces
- DataForSEO adapter subset
- Domain-availability adapter
- Mock fixtures
- Qualification CLI
- Capability report
- Cost logging

Exit criteria:

- Required DataForSEO fields confirmed through fixtures
- Live test optionally passes for one service and three location forms
- Blocking gaps documented

### Milestone 1 — Core application

Deliver:

- Database models
- Alembic migrations
- Seed loaders
- Service/market management
- ScanRun model
- Basic CLI
- Basic dashboard shell

Exit criteria:

- Seed files load idempotently
- One-off service and location records can be created
- All tests pass

### Milestone 2 — Research pipeline

Deliver:

- Keyword discovery
- Keyword metrics
- SERP snapshots
- Competitor metrics
- Provider discovery
- Caching
- Normalization

Exit criteria:

- Fixture scan produces complete normalized data
- Repeated scan uses cache
- Missing data is visible

### Milestone 3 — Scoring and dashboard

Deliver:

- Versioned scoring
- Explainable components
- Opportunity list
- Opportunity detail
- Approval/rejection
- Scan history

Exit criteria:

- No manual labels required
- Scores can be recalculated from stored data
- Each score has a human-readable explanation

### Milestone 4 — Domains and outreach

Deliver:

- Domain candidate generation
- Availability adapter
- Provider ranking
- Outreach templates
- Optional LLM generator
- Manual contact tracking

Exit criteria:

- Approved opportunity produces ranked domains
- Approved provider produces email/call/voicemail drafts
- No message is sent automatically

### Milestone 5 — Site generator

Deliver:

- SiteConfig
- Asset model
- Static templates
- Local build
- Local preview
- Disclosure and provider-independent checks

Exit criteria:

- One complete site builds from structured data
- Site contains no provider identity
- Site passes HTML and smoke tests

### Milestone 6 — Staging deployment

Deliver:

- Cloudflare Pages adapter
- Staging command
- Deployment record
- Smoke test
- Manual-effort log

Exit criteria:

- One sample property deploys to staging
- URL is stored
- HTTP smoke test passes
- No production domain is attached

---

## 29. Definition of done for V1

V1 is complete when a user can:

1. Add services and locations through seed files.
2. Enter a one-off service and location in the UI.
3. Preview resolved market interpretation and estimated API cost.
4. Run a scan.
5. See demand, competition, SERP, provider, and confidence data.
6. Understand exactly why an opportunity received its score.
7. Approve an opportunity.
8. Receive ranked domain suggestions.
9. View potential providers.
10. Generate manually sendable outreach.
11. Create a provider-independent site configuration.
12. Generate and preview one complete static site.
13. Deploy that site to staging.
14. Review all manual interventions required.
15. Repeat the workflow without manually labeling training data.

---

## 30. Codex implementation instructions

1. Implement milestones in order.
2. Do not skip Milestone 0.
3. Do not hard-code live API responses.
4. Create provider interfaces before concrete adapters.
5. Use fixtures for all default tests.
6. Keep live tests opt-in.
7. Run formatting, linting, typing, and tests after every milestone.
8. Update `README.md` with exact setup and run commands.
9. Update this spec only when implementation requires a documented deviation.
10. Do not expand into V2 without an explicit request.
11. Prefer simple, testable implementations over premature abstractions.
12. Preserve raw data and explainability at every stage.
13. When an API capability is uncertain, add a qualification test instead of guessing.
14. Never invent provider, market, or SEO data.
15. Leave the repository in a runnable state after each milestone.

---

## 31. Initial commands expected after implementation

```bash
# Install
uv sync

# Initialize database
uv run alembic upgrade head

# Load example seeds
uv run rank-rent seeds load \
  --services seeds/services.example.yaml \
  --locations seeds/locations.example.yaml

# Run fixture qualification
uv run rank-rent qualify --fixtures

# Run local app
uv run rank-rent web

# Run tests
uv run pytest

# Optional live qualification
ALLOW_LIVE_API_CALLS=true uv run rank-rent qualify --live

# Generate sample site
uv run rank-rent site generate <opportunity_id>

# Preview sample site
uv run rank-rent site preview <opportunity_id>

# Deploy sample site to staging
uv run rank-rent site deploy-staging <opportunity_id>
```

---

## 32. Final constraint

The software’s job is to reduce research, setup, deployment, and outreach preparation work.

It must not hide the remaining business uncertainties:

- Rankings are not guaranteed.
- Provider profitability is unknown until a pilot.
- Lead quality is unknown until real traffic arrives.
- Provider willingness to pay is unknown until outreach.
- Legitimate authority and links may still require manual work.

These uncertainties must be measured after launch, not replaced with fabricated certainty inside the scanner.
