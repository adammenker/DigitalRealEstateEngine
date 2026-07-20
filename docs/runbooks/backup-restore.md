# Backup and Restore Failure

1. Block migrations and releases; do not delete existing snapshots.
2. Inspect backup job logs, encryption access, storage capacity, and retention.
3. Create a new verified snapshot and restore it into an isolated staging database.
4. Run schema, row-count, audit-chain, replay, and application smoke checks.
5. Record recovery point/time results. Escalate immediately if the recovery
   objective cannot be met.

