# Credential Leak and Secret Rotation

1. Revoke the credential immediately and disable affected live operations.
2. Preserve redacted logs/audit records; notify the security owner.
3. Search repository history, CI logs, images, artifacts, and provider logs.
4. Create a least-privilege replacement in the environment's managed secret store.
5. Deploy by secret version, verify readiness without provider calls, then run one
   bounded administrative check.
6. Remove the old version after confirmation and document exposure and spend.

Rotate provider and OIDC credentials at least every 90 days or according to the
provider's stronger policy. Never send values through chat, tickets, or commits.

