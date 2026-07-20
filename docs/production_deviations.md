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

