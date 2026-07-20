# Property, Domain, and Site Operations

Workstream I turns an `approved_for_property` opportunity into a provider-independent
property. It does not buy a domain, change public DNS, or deploy a public website.
Current adapters are offline fixtures and local filesystem deployments only.

## Lifecycle

```text
approved_for_property
  -> property + immutable PropertyVersion
  -> local domain candidates
  -> operator shortlist
  -> offline availability evidence
  -> explicit operator purchase approval
  -> manual registration evidence
  -> exact DNS-record verification
  -> structured SiteConfig
  -> human SiteConfig approval
  -> deterministic preview/staging/production build
  -> human compliance review
  -> local deployment record
  -> rollback to a prior reviewed build
```

`provider_assignments` remains the authoritative provider lifecycle from Workstream J.
Workstream I enriches those rows with reviewed credentials, licenses, claims,
testimonials, hours, service radius, logo, and photo references. Its existing partial
unique index continues to enforce one `active` assignment per property.

Provider replacement changes only provider and routing configuration. It does not
replace the property, domain, analytics configuration, SiteConfig, build history, or
public tracking number.

## Operator API

The Next.js dashboard exposes a Property tab for approved opportunities. The backend
also provides these route groups:

- `POST /api/opportunities/{id}/property`
- `GET /api/properties` and `GET /api/properties/{id}`
- `/api/properties/{id}/domain-candidates/*`
- `/api/domain-candidates/{id}/*`
- `/api/domain-registrations/{id}/*`
- `/api/properties/{id}/assets`
- `/api/properties/{id}/provider-assignments`
- `/api/properties/{id}/site-configs`
- `/api/site-configs/{id}/*`
- `/api/site-builds/{id}/*`
- `/api/properties/{id}/deployments/rollback`

Local/test requests default to the local operator identity. Approval endpoints should
send `X-Actor-Id` and the appropriate `X-Actor-Role` (`operator`, `reviewer`, or
`admin`). Staging and production environments require authenticated actor headers
through the shared review actor dependency.

## Domain Safety

Candidate generation is deterministic and local. Availability checks use explicitly
supplied fixture evidence; this is not proof that a registrar will sell a domain.

The purchase-approval endpoint only creates an approval record. `ManualRegistrarAdapter`
has `can_purchase = false`, and the service rejects any purchase-capable adapter. An
operator must register the domain outside the application and record a receipt or
external reference. DNS is marked verified only when every expected record exactly
matches the submitted observed evidence.

## SiteConfig and Content

Every SiteConfig is structured and versioned:

- brand
- service and market
- pricing guidance
- service process
- FAQs and local considerations
- reviewed provider details
- visible referral disclosure
- calls to action
- approved assets
- metadata and analytics
- form routing

The schema rejects unsupported identity and ranking claims. Provider-specific details
must reference a reviewed assignment. Referenced assets must include provenance,
license metadata, alt text, and human approval.

## Build Validation

Builds are content-addressed and deterministic. A build validates:

- baseline HTML structure and language metadata
- one visible referral disclosure on every page
- canonical, description, Open Graph, and robots metadata
- truthful `WebSite` and `Service` structured data, never fake `LocalBusiness`
- valid sitemap XML
- all internal links
- staging/preview noindex policy
- production indexing policy
- per-page and total byte performance budgets

Preview and staging builds always emit `noindex,nofollow` and `Disallow: /`.

## Production Gates

A production release record requires all of the following:

1. Opportunity still has `approved_for_property`.
2. SiteConfig is approved.
3. Build validation passed.
4. One active reviewed provider, or an explicitly reasoned neutral pilot.
5. Compliance review approved the complete required checklist.
6. Manually registered domain and DNS evidence are verified.
7. Routing profile reports healthy.
8. Property analytics configuration has `verified: true`.
9. An operator explicitly confirms the release with a reason.
10. The adapter is fail-closed and local-only.

The current “production” environment is a local production-shaped artifact and audit
record. It is never a public deployment. A future public adapter requires a separate
reviewed workstream and must not bypass these gates.

## Rollback

Rollback requires operator confirmation and targets a prior production deployment.
It rematerializes that immutable build locally, marks the displaced release rolled
back, and creates a new deployment record with both previous and rollback lineage.

## Verification

```bash
python3 -m pytest -q tests/unit/test_property_workflow.py
python3 -m pytest -q tests/unit/test_migrations.py
python3 -m ruff check src tests
python3 -m mypy --strict src
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build
```
