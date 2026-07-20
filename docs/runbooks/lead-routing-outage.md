# Lead Routing Outage

1. Keep intake durable, stop repeated delivery attempts, and alert the operator.
2. Inspect assignment status, adapter health, retry count, and idempotency key.
3. Switch to an approved fallback adapter or manual delivery queue.
4. Replay undelivered leads exactly once and verify provider acknowledgement.
5. Notify affected providers and record outcomes without exposing lead PII in logs.

