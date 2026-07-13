# Implementation Deviations

This file records known deviations from the remediation steering specification.

## Current Remediation Slice

The steering document is a multi-milestone remediation plan. This implementation slice covers:

- explicit `fixture` / `live` runtime modes;
- fail-fast live-mode validation for missing DataForSEO configuration;
- adapter factory separation so live mode cannot silently instantiate fixture adapters;
- live DataForSEO calls for account checks, location resolution, keyword discovery, keyword metrics, SERP snapshots, backlinks summaries, and business listings;
- API/UI data-mode exposure and fixture-data banner;
- Milestone 0 verification command and current-state documentation.

The following later milestones are not complete yet:

- full real qualification framework;
- scoring rewrite;
- scan planning/cost confirmation;
- paid-request cache integration;
- asynchronous persisted scan jobs;
- typed Alembic-managed persistence;
- approval-gated site generation;
- real domain availability;
- real cloud staging deployment.

Live DataForSEO work is functionally wired but still blocked externally until the configured account is verified in DataForSEO. Domain availability is deliberately returned as `unknown` in live mode until a real availability provider is configured.

## Data Mode Persistence

The steering spec requires `data_mode` and adapter versions on every `ScanRun`. To avoid a schema change before the migration milestone, this slice records those values in the existing `ScanRun.integration_versions` and `ScanRun.request_parameters` JSON fields. A later migration should promote `data_mode` into a typed column.
