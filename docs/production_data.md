# Production Database and Durable Raw-Response Storage

Workstream C establishes the production persistence boundary without changing authentication,
property, lead, or provider-call behavior.

## Supported Runtime Profiles

`APP_ENV=production` requires a PostgreSQL SQLAlchemy URL. SQLite remains supported for local
development, fixture tests, and lightweight replay tests.

Production example:

```env
APP_ENV=production
DATABASE_URL=postgresql+psycopg://rank_rent:secret@postgres/rank_rent
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=1800
DATABASE_STATEMENT_TIMEOUT_MS=30000
DATABASE_TRANSACTION_TIMEOUT_MS=60000
```

The PostgreSQL engine enables connection pre-ping, bounded pooling, statement timeout, and
`idle_in_transaction_session_timeout`. Web requests and scan workers use separate session
factories; each execution context owns and closes its own SQLAlchemy `Session`.

`GET /healthz` is a process liveness check. `GET /readyz` verifies database connectivity and
requires the database's Alembic revision to equal the application schema head.

## Raw Response Blobs

New responses written by the live DataForSEO adapter are sanitized before persistence. Their
canonical JSON bytes are stored outside PostgreSQL, while `raw_api_responses` records:

- object key, SHA-256 checksum, content type, and byte size;
- storage backend and encryption status;
- provider, endpoint, normalized parameters, API/shape version, and provider identifiers;
- source scan lineage, request/response timestamps, cost, retention classification, and blob
  creation time.

Blob content is create-once: filesystem writes use an atomic link and S3-compatible writes use
`If-None-Match: *`; a concurrent write with different bytes fails. ORM updates to raw-response
content or lineage metadata also fail. Cache expiry may still change because it is operational
cache state rather than purchased evidence.

Filesystem configuration:

```env
BLOB_STORE_BACKEND=filesystem
BLOB_STORE_PATH=/durable/raw-responses
```

S3-compatible configuration:

```env
BLOB_STORE_BACKEND=s3
BLOB_STORE_S3_BUCKET=rank-rent-production
BLOB_STORE_S3_PREFIX=raw-data
BLOB_STORE_S3_REGION=us-east-1
BLOB_STORE_S3_ENDPOINT_URL=
BLOB_STORE_S3_SERVER_SIDE_ENCRYPTION=AES256
```

Install the optional adapter dependency with `pip install -e ".[s3]"`. The repository Docker image
already includes that extra. The adapter uses the standard AWS credential chain and supports a
custom endpoint for compatible object stores. Tests use an injected in-memory client and never
contact S3.

Legacy rows with inline `response_json` remain readable. New live-adapter rows use the blob as
the authoritative response body and leave the legacy JSON field empty. Database replay and
bundle export verify the stored checksum before returning evidence.

## Migration Policy

Revision `c9a4e7d2b6f1` is the Workstream C migration and directly follows the prior head
`b7d2f4a9c6e1`. Workstream D revision `6f4c2d8a9b17` follows it as the current head. The C revision
adds nullable blob-location fields so existing prototype rows remain readable, plus required
retention/encryption classifications for forward writes. SQLite and PostgreSQL both use this
single linear Alembic chain.

Disposable prototype databases may still be wiped at the declared production cutover. From
that cutover onward, upgrades must use forward migrations; production databases must not be
auto-stamped over unknown schemas.

## Backup, Restore, and Retention Runbook

Initial recovery objectives are RPO 24 hours and RTO 4 hours.

Before production launch, the operator must configure and verify:

1. Automated encrypted PostgreSQL backups at least daily.
2. Point-in-time recovery when the PostgreSQL service supports WAL archiving.
3. Object-store versioning and provider-managed encryption; filesystem deployments require an
   encrypted volume and independently backed-up blob directory.
4. A restore into an isolated environment, followed by Alembic revision, `/readyz`, row-count,
   and sampled blob-checksum validation.
5. Alerting when the most recent successful database or blob backup is older than 24 hours.

The application records `raw_provider_response` as the retention classification. Raw paid
responses, API-call ledger rows, scan plans, scores, and audit evidence should initially be
retained indefinitely unless a legal/manual deletion request applies. Provider contact data,
lead data, logs, and deleted-opportunity retention are outside Workstream C because those
models do not exist yet.

No production backup scheduler, cloud bucket, PostgreSQL service, or legal-deletion workflow is
created by this repository change. `tests/integration/test_postgres_concurrency.py` exercises
atomic worker claims and daily spend reservations when `TEST_POSTGRESQL_URL` points to a disposable
database; the default no-service CI run skips it. A concurrency run against the selected hosted
PostgreSQL service and a restore rehearsal remain deployment gates. No verification path makes
live provider calls.
