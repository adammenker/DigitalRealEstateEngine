# ADR 0001: Production Database

Status: Accepted

Use PostgreSQL for staging and production. Keep SQLite for local development, fixtures,
and lightweight replay tests. Production job claiming and concurrent review use
PostgreSQL row locks and transactional uniqueness constraints. The pre-production
cutover creates one clean baseline; forward migrations are mandatory afterward.

This decision trades a small operational burden for durable concurrency, recovery, and
well-understood backup support.

