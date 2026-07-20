# ADR 0005: Deployment Platform

Status: Accepted

Describe infrastructure with Terraform. The reference production topology uses
containerized API, worker, and frontend services, managed PostgreSQL, S3-compatible
object storage, a managed secret store, OIDC, TLS ingress, and centralized telemetry.

Infrastructure modules must keep local, test, staging, and production isolated. The
specific cloud account and DNS provider remain operator choices; public deployment
requires manual approval and immutable release metadata.

