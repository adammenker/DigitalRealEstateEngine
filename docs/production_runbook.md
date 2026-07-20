# Production Runbook

## Default Safety Posture

Production paid calls and public deployments are disabled unless all relevant gates are
true. During an incident, stop new work before attempting repair:

1. Set the paid-call kill switch.
2. Disable full scans.
3. Pause workers or public routing when applicable.
4. Preserve logs, audit events, call ledgers, and deployment identifiers.
5. Identify the last known-good application, configuration, dataset, and database state.
6. Restore or roll forward using an approved runbook.

## Incident Index

| Incident | Immediate action |
|---|---|
| DataForSEO overspend | Kill paid calls, pause workers, reconcile provider billing |
| Provider outage | Open circuit, preserve jobs for bounded retry |
| Database outage | Stop writes, fail readiness, invoke database recovery |
| Worker stuck | Stop claims, inspect leases, recover only stale jobs |
| Bad scoring release | Freeze rankings, select prior config version, rescore explicitly |
| Corrupt public dataset | Deactivate dataset and restore prior atomic version |
| Credential leak | Kill affected integration, rotate secret, audit usage |
| Bad public deployment | Roll back deployment and disable traffic if needed |
| Lead-routing outage | Pause intake or show safe unavailable state, alert operator |
| Lost provider routing | Preserve public number, pause assignment, activate reviewed replacement |

Detailed provider, database, deployment, security, and lead-routing procedures are kept
in their dedicated documentation. Never test incident recovery with a paid call unless
an operator explicitly approves it.

