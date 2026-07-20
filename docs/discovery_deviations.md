# Discovery Completion Deviations

The implemented discovery system intentionally keeps several boundaries narrow.

## Intentional Scope Limits

- Geography is limited to canonical U.S. populated places and ZCTAs. Address-level and
  international discovery are outside the current product scope.
- The public-data prefilter uses a documented ACS signal set. Business-count, climate,
  service-incidence, and other datasets remain deferred until their value is validated.
- Population-share demand is retained as low-confidence estimated evidence; it is not a
  substitute for measured local demand.
- The service catalog covers the configured initial families. Unconfigured drafts may be
  tested but cannot produce full rankable assessments.
- Page-level competitor metrics remain unavailable when the provider returns only domain
  metrics. The engine records the distinction instead of manufacturing page evidence.
- Async execution uses a database-backed in-process worker rather than an external queue.
- Discovery does not include domain acquisition, site generation, outreach, lead routing,
  billing, or launch approval workflows.

## Remaining Validation

The architecture and offline replay path are implemented, but production claims require
empirical calibration with representative live evidence and outcomes. Production database,
operations, security, and downstream product workflow also remain open in
`docs/production_backlog.md`.
