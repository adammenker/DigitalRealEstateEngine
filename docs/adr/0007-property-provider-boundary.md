# ADR 0007: Property and Provider Configuration Boundary

Status: accepted

## Context

The discovery prototype already had sample-site `domain_candidates`, `site_configs`,
and `deployments` tables. Workstream J later introduced durable routing profiles and
`provider_assignments`. Workstream I needs a production-shaped property lifecycle
without rewriting lead routing or silently treating prototype rows as reviewed
production records.

## Decision

1. New production workflow tables use explicit names such as
   `property_domain_candidates`, `property_site_configs`, and
   `property_deployments`. Legacy prototype tables remain untouched.
2. A `Property` has a stable string ID shared with `PropertyRoutingProfile`.
3. Workstream J's `provider_assignments` table is authoritative. Workstream I adds
   reviewed presentation fields and aliases it as `ActiveProviderAssignmentORM`.
4. The existing partial unique index enforces exactly one active assignment.
5. Provider replacement is a routing/presentation configuration operation. Property,
   domain, analytics, tracking number, SiteConfig, and build history are preserved.
6. Domain purchase, public DNS mutation, and public deployment are unavailable in
   current adapters.
7. Builds, property versions, and compliance reviews are immutable historical records.

## Consequences

- Lead routing and property production cannot drift into separate provider states.
- Existing fixture and prototype records remain inspectable but cannot satisfy
  Workstream I gates.
- A future public deployment adapter must be separately reviewed and explicitly
  enabled; adding an adapter alone cannot bypass service-level release gates.
