from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticIncident:
    name: str
    metric: str
    sample_value: float
    threshold: float
    comparison: str
    runbook: str

    def fires(self) -> bool:
        if self.comparison == "gt":
            return self.sample_value > self.threshold
        if self.comparison == "gte":
            return self.sample_value >= self.threshold
        if self.comparison == "lt":
            return self.sample_value < self.threshold
        raise ValueError(f"Unknown incident comparison: {self.comparison}")


SYNTHETIC_INCIDENTS = (
    SyntheticIncident(
        "api_unavailable",
        "api_error_ratio",
        0.02,
        0.005,
        "gt",
        "docs/runbooks/database-outage.md",
    ),
    SyntheticIncident(
        "database_unavailable",
        "database_ready",
        0,
        1,
        "lt",
        "docs/runbooks/database-outage.md",
    ),
    SyntheticIncident(
        "worker_unavailable",
        "worker_heartbeat_age_seconds",
        120,
        60,
        "gt",
        "docs/runbooks/worker-stuck.md",
    ),
    SyntheticIncident(
        "queue_age",
        "oldest_queued_seconds",
        600,
        300,
        "gt",
        "docs/runbooks/worker-stuck.md",
    ),
    SyntheticIncident(
        "scan_failures",
        "scan_failures_15m",
        4,
        3,
        "gte",
        "docs/runbooks/provider-outage.md",
    ),
    SyntheticIncident(
        "cost_limit",
        "unexpected_cost_usd",
        11,
        10,
        "gt",
        "docs/runbooks/dataforseo-overspend.md",
    ),
    SyntheticIncident(
        "backup_failure",
        "backup_age_hours",
        30,
        24,
        "gt",
        "docs/runbooks/backup-restore.md",
    ),
    SyntheticIncident(
        "restore_failure",
        "restore_check_success",
        0,
        1,
        "lt",
        "docs/runbooks/backup-restore.md",
    ),
    SyntheticIncident(
        "authentication_anomaly",
        "authentication_failures_10m",
        30,
        25,
        "gt",
        "docs/runbooks/credential-leak.md",
    ),
    SyntheticIncident(
        "deployment_health",
        "deployment_ready",
        0,
        1,
        "lt",
        "docs/runbooks/bad-public-deployment.md",
    ),
    SyntheticIncident(
        "lead_routing",
        "routing_failures_10m",
        3,
        2,
        "gt",
        "docs/runbooks/lead-routing-outage.md",
    ),
)

