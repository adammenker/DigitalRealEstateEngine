# Lead Privacy

## Data minimization

The lead record stores only routing fields: name, email and/or phone, optional
postal code and message, property, assignment, status, and lifecycle dates.
Raw IP addresses and user agents are not persisted. The request fingerprint,
deduplication key, and subject key use HMAC-SHA256 with an operator-supplied
pepper.

Consent and referral disclosure are versioned and timestamped. Consent proof
stores the internal request ID and a private request fingerprint, not the raw
network address. The exact server-configured consent and referral-disclosure
copy is stored with its version. Submissions carrying a stale version are
rejected before persistence. Production operators must replace and legally
approve the default copy before public activation.

## Logging

Application logs use lead and property identifiers. `request_log_context()` and
`redact_pii()` remove contact, message, address, destination, credential, and
network fields before structured logging. Delivery errors persist stable codes
and generic summaries. Destination references are masked.

Adapter implementations must not log `DeliveryRequest`, because that
short-lived object necessarily contains the contact details needed to route a
lead.

## Access

`LeadPrivacyService` enforces these roles:

| Role | Lead export | Lead deletion |
|---|---:|---:|
| operator | yes | yes |
| privacy admin | yes | yes |
| assigned provider | assigned leads only | no |
| analytics | no | no |

Outcome exports allow operator, privacy admin, and analytics roles. Outcome
source deletion and retention enforcement require privacy admin.

These are service-layer controls. Public endpoints must enforce an
authenticated actor and construct `AccessContext` from server-side identity;
clients must never submit their own role or assignment set.

## Deletion

Lead deletion is an irreversible application-level anonymization. It removes
name, email, phone, postal code, message, and request proof metadata while
preserving non-PII lifecycle, consent-version, delivery, and aggregate event
records for operational accounting. A deleted lead cannot be restored.

Property outcome imports contain aggregate metrics rather than lead PII.
Privacy admins can remove all records from one imported source or enforce an
age cutoff.

## Encryption and recording

Production database, backup, transport-encryption, secret-reference, and authenticated-access
contracts are implemented, but they have not been exercised in a provisioned production
environment. This module does not implement home-grown field encryption. It must not be publicly
enabled until encrypted storage, managed secret injection for the HMAC pepper, route permissions,
audit events, and authenticated access are verified in staging.

Call recording is off by default. Enabling it requires explicit approval and a
retention period. Legal notice, jurisdiction review, provider support, secure
audio storage, access logs, and deletion must be implemented by the selected
call-tracking adapter before recording can be used.
