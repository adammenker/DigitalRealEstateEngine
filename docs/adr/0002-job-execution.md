# ADR 0002: Job Execution

Status: Accepted

Run scan workers as a process separate from the API. Use a PostgreSQL-backed lease queue
with atomic claim, heartbeat, bounded retry with jitter, cancellation, stale recovery,
poison quarantine, and idempotent stages. A paid call requires a reserved unique planned
request, valid qualification, available daily budget, and all kill switches enabled.

The database queue keeps transactional scan and cost state together at the current
scale. A dedicated broker may be introduced only after measured need.

