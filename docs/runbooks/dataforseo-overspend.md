# DataForSEO Overspend

**Trigger:** unexpected paid call, cost cap breach, or ledger mismatch.

1. Set `ALLOW_LIVE_API_CALLS=false`, stop workers, and preserve API/audit logs.
2. Revoke or cap the environment credential in DataForSEO.
3. Reconcile `scan_plan_calls`, `api_calls`, provider task IDs, and invoices.
4. Identify any unplanned or duplicate call before resuming fixture/replay work.
5. Rotate credentials, deploy the fix, run no-network calibration and replay,
   then re-enable live calls with an administrator-approved low limit.

