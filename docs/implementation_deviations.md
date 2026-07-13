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
- model-driven startup initialization for disposable local test databases;
- typed scan records for plan calls, keyword metrics, SERPs, competitors, and providers;
- queued background scan jobs with status endpoints;
- API/UI data-mode exposure, fixture-data banner, cost confirmation, and recent scan status;
- Milestone 0 verification command and current-state documentation.

The following later milestones are not complete yet:

- full real qualification framework;
- scoring rewrite;
- cancellation, retry locking, and durable worker orchestration beyond in-process background jobs;
- approval-gated site generation;
- registrar-grade domain availability;
- real cloud staging deployment.

Live DataForSEO work is functionally wired. Live/replay domain checks use a no-credit DNS signal provider instead of mock data; registrar-grade availability still requires a dedicated provider. DataForSEO HTTP 402 billing failures are surfaced explicitly.

## Offline Remediation Deviations

The offline remediation specification is intentionally broad. This implementation completed the credit-safety and replay foundation but leaves these items for later slices:

- raw response storage uses the existing `raw_api_responses` table rather than the full typed `StoredApiResponse` table shape;
- checksums are computed by the replay model but not persisted as a dedicated column;
- cache TTL/expiry and force-refresh confirmation are not fully implemented;
- scan planning uses maintained endpoint estimates rather than provider price-table extraction;
- async scans are in-process background jobs; cancellation, retry locking, and external queue workers are not implemented;
- geographic resolution still uses the existing market model plus limited offline coordinates rather than a complete U.S. city/ZIP dataset;
- scoring remains version `v1` with targeted live/preliminary labeling fixes rather than a full version-2 scoring rewrite;
- Alembic migrations and old-local-database compatibility are intentionally deferred. During the testing phase, local DB data is disposable and schema changes should be handled with `rank-rent reset-db --confirm` or `docker compose down -v`.

## Data Mode Persistence

The steering spec requires `data_mode` and adapter versions on every `ScanRun`. This slice records those values in `ScanRun.integration_versions` and `ScanRun.request_parameters`. If/when production data needs durable compatibility, `data_mode` can be promoted into a typed column from a fresh baseline.
