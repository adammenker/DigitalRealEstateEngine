# Lead Routing

## Implemented boundary

Lead routing is an internal service layer under `rank_rent.lead_routing`. This
workstream does not expose a public form endpoint and cannot send a real email,
text message, or phone call. The included adapters are deterministic fixtures.

The intake path is:

```text
validated LeadForm
-> idempotency lookup
-> request rate-limit hook
-> contact/time-window deduplication
-> local spam assessment
-> consent and referral-disclosure record
-> durable lead commit
-> active provider lookup
-> channel delivery with stable idempotency key and retries
-> delivery event or operator alert
```

`LeadForm` requires a property ID, name, email or phone, explicit consent,
referral-disclosure acknowledgement, and an idempotency key. It normalizes
contact data and rejects malformed or overlong fields before persistence.

## Delivery behavior

`DeliveryAdapter` is the retryable email/phone boundary. A delivery uses the
same `delivery_key` for every retry, while each attempt receives a unique
attempt number. The database stores both the logical `ProviderDelivery` and
each `RoutingAttempt`. A successful fixture adapter is idempotent for the
delivery key.

The service commits the lead and each attempt before awaiting an adapter. A
slow adapter therefore does not keep a database write transaction open.
Failures use stable error codes and PII-free summaries. After every channel is
exhausted, `OperatorAlertAdapter.routing_failure()` receives only property,
lead, and reason identifiers.

## Call routing

`CallTrackingAdapter` supports route configuration and health checks.
`PropertyRoutingProfile` owns the public tracking number. A
`ProviderAssignment` owns the replaceable destination. Recording defaults off
and requires both explicit approval and a retention period.

`FixtureCallTrackingAdapter` stores routes in memory. It does not provision a
number, forward a call, record audio, or contact a provider.

## Analytics truth

Analytics events persist `source_type`, `source_name`, and `truth_basis`.
Provider-reported events must identify a provider source; operator-verified
events must identify an operator source. Outcomes such as appointment, won
job, lost job, and revenue are mirrored into typed `LeadOutcome` records when
they have a lead ID. Unknown or inferred outcomes are never promoted to
observed facts.

## Production integration

A production adapter must provide shared rate limiting, authenticated operator
alerts, encrypted delivery credentials, provider-side idempotency, and
documented timeout/retry semantics. It must pass the fixture contract tests
before activation. Shared authentication, authorization, CORS, rate-limit, observability, and
production-storage foundations now exist, but no reviewed lead HTTP contract or real delivery,
call-tracking, or alert adapter has been selected. Public form endpoints remain blocked until
those contracts, provider adapters, route-specific permissions, audit events, abuse controls,
staging exercises, and privacy approval are complete.
