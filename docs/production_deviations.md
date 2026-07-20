# Production Deviations

This file records differences between the master specification and delivered behavior.
It is not a place to silently waive launch blockers.

## DEV-001: Local Orchestration Branches

Subagents use isolated worktrees and commits for conflict control, while the final
integrated code remains on `main` in accordance with the repository's existing workflow.

## DEV-002: External Production Resources

Cloud accounts, PostgreSQL, object storage, OIDC, DNS, alert destinations, email, call
tracking, and production provider credentials cannot be provisioned safely without the
operator's accounts and explicit approvals. The repository will provide adapters,
configuration validation, infrastructure definitions, fixture implementations, and
fail-closed gates. A gate requiring a real provider or restore rehearsal remains blocked
until the operator completes that external action.

## DEV-003: Public Launch

Implementing Release B code does not authorize a public launch. Domain purchase,
provider activation, compliance approval, and production deployment remain explicit
operator actions after Release A approval.

## DEV-004: Addressable-Market CLI

The addressable-market batch and top workflows are implemented through service APIs and
`scripts/addressable_market.py`. The exact `rank-rent prefilter batch/top` command registration
specified by the master document is still pending.

## DEV-005: Property Security Integration

Production authentication is globally fail-closed, but the centralized mutation-policy table and
append-only audit integration do not yet cover every property/domain/site mutation. Release A and
deployed property staging remain blocked until explicit permissions and audit tests cover those
routes.

## DEV-006: Lead And Outcome Provider Boundary

Lead and outcome models, durable services, privacy controls, and fixture adapters are implemented.
No public lead API or real delivery, call-routing, alert, registrar, hosting, analytics, or outcome
adapter is approved. Provider-specific implementation is deferred until vendors and contracts are
selected.
