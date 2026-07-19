# Discovery Architecture

The discovery process is the engine UI/backend path for deciding whether a service-market opportunity is worth reviewing. It does not generate domains, outreach, sites, or deployments by default.

## Flow

1. Resolve service and market input.
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

The discovery report is built only after all scan API calls have terminal statuses and the
actual scan cost has been assigned. `scan_metadata.api_cost_ledger` contains reconciled call,
cache-hit, failure, estimated-cost, and actual-cost totals plus the individual call rows.
