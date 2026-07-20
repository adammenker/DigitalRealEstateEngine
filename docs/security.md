# Production Security

## Authentication and authorization

Local and test environments use the explicit `local` adapter. It creates a
development identity from `X-Local-User` and `X-Local-Role`, defaulting to a
local administrator so the existing local UI remains usable. This adapter is
rejected in staging and production.

Staging and production require OIDC bearer tokens. The API validates signature,
issuer, audience, expiry, issued-at time, subject, algorithm, and a configured
role claim against an allowlisted HTTPS JWKS host. Missing or invalid
configuration prevents startup. The recognized roles are `admin`, `operator`,
`reviewer`, and `read_only`; permissions are centralized in
`rank_rent.security.auth.ROLE_PERMISSIONS`.

All production backend routes require authentication except `/live`, `/ready`,
`/healthz`, `/readyz`, and `/health/dependencies`. Read operations require an authenticated
role. Mutation policies enforce scan, review, deployment, routing, export, and
deletion permissions. Full scans require `run_full_scan`; no anonymous or
read-only request can confirm or trigger paid work.

Bearer authentication does not use browser session cookies, so CSRF does not
apply to API calls. An identity-provider frontend integration must use
Authorization Code with PKCE and send the access token as a bearer token. Do not
store access tokens in local storage. Any future cookie session must use
`Secure`, `HttpOnly`, and `SameSite=Strict` and add synchronizer-token CSRF
protection.

## Audit

Successful mutations are written to `audit_events` with actor, role, target,
request ID, timestamp, metadata, previous hash, and event hash. Login/logout,
scan creation, promotion, cost confirmation, cancellation, retry, and rescore
are covered by current routes, as are review transitions, ownership, evidence
overrides, templates, and batch plans. Future domain, deployment, routing,
export, and deletion routes must call `append_audit_event` in the same
transaction as their state change.

ORM listeners and database triggers reject update/delete. Access to
`GET /api/audit-events` is admin-only. Audit storage must be exported to
write-once retention storage in production.

## Web controls

The API applies CSP, HSTS in staging/production, frame denial,
`nosniff`, strict referrer and permissions policies, no-store caching, a body
size limit, per-identity rate limiting, strict CORS, safe error bodies, and input
validation. URL-fetching adapters must call `validate_outbound_url` before a
request; it rejects credentials, non-HTTPS schemes, local names, private IPs,
DNS rebinding targets, and hosts outside an allowlist.

Local/test rate limiting is in-process. Staging and production fail startup
unless the Redis backend and a TLS `rediss://` URL are configured, so limits
remain consistent across API replicas. Redis failure fails requests closed.

Uploads are not enabled. Before adding one, define MIME allowlists, content
inspection, size limits, randomized object names, malware scanning, and private
object storage.

## Secrets

Production receives secrets from the runtime platform's managed secret store.
The repository, build context, image layers, release manifests, logs, Terraform
variables, and CI output must never contain values. `env://` and
`file:///run/secrets/...` references are supported for non-managed runtimes;
AWS Secrets Manager and Vault references document ownership while the platform
injects resolved values at runtime.

Use one credential per environment and provider, scoped to the minimum API
permissions and cost ceiling. See [secret rotation](runbooks/credential-leak.md).
