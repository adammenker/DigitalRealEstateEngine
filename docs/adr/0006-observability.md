# ADR 0006: Observability

Status: Accepted

Emit redacted JSON logs, Prometheus-compatible metrics, and OpenTelemetry trace context.
Correlate requests, scans, opportunities, planned calls, users, and deployments. Health
and readiness endpoints may inspect dependencies but must never issue paid calls.

Initial SLOs follow the master specification. Alerts route through configurable adapters
so local and CI tests use in-memory sinks while staging and production use managed alert
destinations.

