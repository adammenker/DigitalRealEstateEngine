# Deployment and Rollback

## Environments

`local`, `test`, `staging`, and `production` have separate databases, object
storage, credentials, cost limits, domains, logs, metrics, identity audiences,
and Terraform state. Staging and production fail startup unless OIDC, HTTPS
CORS, PostgreSQL, secret injection, and environment isolation are valid.

Local Compose starts PostgreSQL, a one-shot migration, API, durable worker, and
frontend. API and worker use the same immutable backend image but different
commands. Run:

```bash
docker compose up --build -d
docker compose ps
curl http://localhost:8011/ready
```

## Release sequence

The approval-gated release workflow runs verification and security checks,
publishes Git-SHA-tagged images, captures image digests, verifies backups, runs
a one-shot migration, deploys API and worker, deploys frontend, performs
no-cost smoke checks, and only then enables traffic. Production uses the GitHub
`production` environment and must have required reviewers configured.

Every release manifest records Git SHA, API/worker/frontend digests, migration,
scoring, evidence-quality, service-catalog, geography, and prefilter versions,
plus release notes. Runtime metadata is queryable at `GET /api/release`.

## Rollback

1. Disable traffic to the unhealthy revision and pause live scans.
2. Select a prior immutable release manifest and run
   `scripts/rollback.sh ENVIRONMENT GIT_SHA`.
3. Roll API, worker, and frontend together unless compatibility is documented.
4. Restore configuration, scoring, and datasets through their versioned
   activation/rollback mechanisms.
5. Downgrade a migration only when its migration note and backup test prove it
   safe. Otherwise restore the application and forward-fix the schema.
6. Run `/live`, `/ready`, `/health/dependencies`, fixture E2E, and a replay scan.
7. Re-enable traffic and record an audit/incident timeline.

Rehearse staging rollback before the first production release and quarterly
afterward.

