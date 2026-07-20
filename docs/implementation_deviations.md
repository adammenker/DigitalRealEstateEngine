# Implementation Deviations

This file records known deviations from the remediation steering specification.

## Current Remediation Slice

The steering document is a multi-milestone remediation plan. This implementation slice covers:

- explicit `fixture` / `live` runtime modes;
- explicit `replay` runtime mode;
- fail-fast live-mode validation for missing DataForSEO configuration;
- adapter factory separation so live mode cannot silently instantiate fixture adapters;
- live DataForSEO calls for account checks, location resolution, keyword discovery, keyword metrics, SERP snapshots, backlinks summaries, and business listings;
- cache-backed live DataForSEO requests where a DB session is available;
- database replay transport and DataForSEO replay adapter;
- scan planning with cache-aware endpoint estimates, exact request payloads where known, and budget blocking;
- low-cost testing scans written as preliminary assessments rather than full ranked scores;
- default scan lifecycle separation from generated sites, domains, and outreach;
- Alembic-backed startup initialization for file-backed local and Docker databases;
- typed scan records for plan calls, keyword metrics, SERPs, competitors, and providers;
- typed keyword clusters and keyword decisions for inclusion, exclusion, grouping, ranking,
  and representative SERP selection;
- database-backed queued scan jobs with status, cancellation, retry, atomic claim, heartbeat,
  and stale-worker recovery;
- discovery completion with scoring `v2`, demand modeling, SERP classification evidence,
  competitor relevance, provider suitability, no-cost rescoring, and comparison/report APIs;
- API/UI data-mode exposure, fixture-data banner, cost confirmation, and recent scan status;
- Milestone 0 verification command and current-state documentation.

The following later milestones are not complete yet:

- automated execution of the live qualification matrix (recording, expiry, and enforcement are implemented);
- an external broker beyond the dedicated database-backed worker process;
- approval-gated site generation;
- registrar-grade domain availability;
- real cloud staging deployment.

Live DataForSEO work is functionally wired. Live/replay domain checks use a no-credit DNS signal provider instead of mock data; registrar-grade availability still requires a dedicated provider. DataForSEO HTTP 402 billing failures are surfaced explicitly.

## Offline Remediation Deviations

The offline remediation specification is intentionally broad. This implementation completed the credit-safety and replay foundation but leaves these items for later slices:

- raw response storage extends the existing `raw_api_responses` table rather than renaming it to `stored_api_responses`;
- scan planning uses maintained endpoint estimates rather than provider price-table extraction;
- async scans use a dedicated database-backed worker process; an external broker remains deferred;
- geographic resolution now uses a versioned offline U.S. city/ZCTA index; address-level and
  international geography remain outside the selected production scope;
- scoring is now version `v2`; remaining scoring work is real-market calibration after production
  DataForSEO evidence exists;
- migrations are restored, but migration coverage is currently an upgrade-head smoke test rather than a large populated historical fixture matrix.

## Data Mode Persistence

The steering spec requires `data_mode` and adapter versions on every `ScanRun`. This slice now stores those values in typed columns as well as keeping the historical JSON metadata for UI/backward compatibility.
