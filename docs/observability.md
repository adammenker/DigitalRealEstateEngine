# Observability and SLOs

## Telemetry

Logs are one-line JSON and include timestamp, level, environment, service,
release version, request ID, trace ID, scan/opportunity/planned-request IDs,
user ID, event, duration, cost, provider endpoint, and error type when known.
Credential-like keys, bearer values, and email addresses are redacted. Request
and W3C `traceparent` IDs flow from the API into scan context, worker events,
provider calls, and persistence logs.

`/metrics` exposes Prometheus metrics and requires authentication in production.
The dashboard in `deploy/observability/dashboard.json` covers API availability,
queue/worker health, provider cost, discovery outcomes, and routing failures.
Alert rules are in `deploy/observability/alerts.yml`. Health checks do not call
DataForSEO or any other paid provider:

- `/live`: process liveness only.
- `/ready`: required configuration, database, and worker readiness.
- `/health/dependencies`: safe dependency details and an explicit
  `paid_provider_probe_performed: false`.

## Initial service levels

| Objective | Indicator | Target | 30-day error budget |
|---|---|---:|---:|
| API availability | Non-5xx authenticated API responses / requests | 99.5% | 3h 36m |
| Queued scan pickup | Scans claimed before 5 minutes / queued scans | 99% | 1% |
| No duplicate paid calls | Unique planned IDs / paid calls | 100% | 0 |
| No unplanned paid calls | Planned paid calls / paid calls | 100% | 0 |
| Cost reconciliation | Reconciled completed scans / completed scans | 100% | 0 |
| Data loss | Confirmed loss incidents | 0 | 0 |

Any zero-budget breach pauses live scans and releases until reviewed. When the
availability or pickup budget is 50% consumed in the first half of a window,
freeze non-remediation releases. At 100%, disable live scans and execute the
relevant runbook.

## Synthetic exercises

In staging, exercise alerts quarterly by stopping the worker, pointing a test
deployment at a refused database port, generating rejected auth requests,
queueing a fixture scan with the worker stopped, and incrementing the fixture
routing-failure path. Cost alerts must be tested with injected metrics, never
paid calls. Record timestamps, alert delivery, owner acknowledgement, and
runbook outcome.

