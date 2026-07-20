# ADR 0004: Authentication

Status: Accepted

Use OIDC with a managed identity provider in staging and production. Validate issuer,
audience, signature, expiry, and role claims server-side. Development and tests may use
an explicit non-production identity adapter; production fails closed when OIDC is not
configured. Do not store application passwords or expose secret values.

Roles are admin, operator, reviewer, and read-only. Privileged mutations are authorized
server-side and append an attributable audit event.

