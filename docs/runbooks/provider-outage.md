# Provider Outage

1. Disable new live scans; leave fixture, replay, and public-data prefiltering on.
2. Confirm provider status using a standalone administrative check, never a scan.
3. Preserve queued work and allow bounded retries only for idempotent requests.
4. Validate schemas and cost ledgers after recovery before restarting workers.
5. Escalate repeated failures and record provider duration/error metrics.

