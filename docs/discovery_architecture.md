# Discovery Architecture

The discovery process is the engine UI/backend path for deciding whether a service-market opportunity is worth reviewing. It does not generate domains, outreach, sites, or deployments by default.

## Flow

1. Resolve the market against the versioned offline U.S. city/ZCTA index. Exact city/state
   and ZIP inputs can resolve automatically; ambiguous or fuzzy inputs require selection.
2. Build a scan plan with planned request IDs, estimated cost, cache status, and request limits.
3. Discover keyword candidates and record candidate decisions.
4. Fetch keyword metrics for included candidates.
5. Cluster close variants, select SERP representatives, and score keyword value.
6. Fetch SERPs for representative keywords and classify each result.
7. Fetch competitor metrics for organic results when the scan profile allows it.
8. Fetch local provider candidates and score provider suitability.
9. Run Scoring V2.
10. Finalize scan status, completion time, and the actual API cost ledger.
11. Persist typed records plus a JSON `discovery_report` artifact.

## Data Modes

- `fixture`: deterministic local data, no network calls.
- `replay`: stored DataForSEO responses through live normalizers, no network calls.
- `live`: DataForSEO adapter. Sandbox is the default host for free mock responses.

## Cost Attribution

`scan_plan_calls` stores intended requests. `api_calls` stores actual cache hits, requests, failures, provider IDs, and actual cost by scan. Sandbox calls record zero actual cost.

Before live planning, the market's geography ID, coordinates, population, reference
population, and provider-search radius are checked against the offline index. Provider
discovery always includes `location_coordinate`; no country-only fallback is permitted.

An attached live market scan must reserve one unused persisted `scan_plan_calls` row before
using a cached response or opening the DataForSEO HTTP client. A missing match, an exhausted
match, or a concurrent attempt to consume the same planned request fails closed before any
network request. Sessionless qualification checks and callers that explicitly opt into
administrative unplanned requests follow a separate policy.

The discovery report is built only after all scan API calls have terminal statuses and the
actual scan cost has been assigned. `scan_metadata.api_cost_ledger` contains reconciled call,
cache-hit, failure, estimated-cost, and actual-cost totals plus the individual call rows.
