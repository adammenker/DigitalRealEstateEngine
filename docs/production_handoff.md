# Production Handoff

## Orchestrator Scope

The orchestrator owns baseline documentation, cross-workstream contracts, migration
reconciliation, backend and frontend integration, verification, and release-gate status.

## Delegated Work

| Owner | Workstreams | Scope |
|---|---|---|
| Franklin | A | Offline calibration, benchmarks, CLI, reports |
| McClintock | B | Addressable-market public-data prefilter V2 |
| Halley | C, D | PostgreSQL/blob storage, worker and cost controls |
| Schrodinger | E, F, G | Security, observability, environments and deployment |
| Hume | H, I | Opportunity review, property and site workflow |
| Euler | J, K | Lead operations, outcomes and feedback reporting |
| Orchestrator | L | Independent integration QA and release validation |

Each delegated handoff must list scope, files, schemas, interfaces, tests, commands,
security implications, operational implications, limitations, deviations, follow-ups,
and its commit SHA. Integration does not imply a production gate has passed.

## Current Handoff State

All implementation tracks are in progress. Release A and Release B remain closed.

