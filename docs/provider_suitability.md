# Provider Suitability

Provider suitability is deterministic local scoring over stored provider candidates.

The score is composed from five separately persisted, configuration-driven signals:

- `service_fit`: matches normalized listing categories and service entries against the service family's `provider_categories`, with service/name language used only as fallback evidence.
- `geographic_fit`: uses Haversine distance when provider and market coordinates are available. Explicit service-area and listing-address matches are fallback evidence; a shared state token alone is not enough.
- `status_certainty`: confirmed operating statuses receive full credit, unknown status receives partial credit, and inactive/closed businesses are capped below the suitable threshold.
- `contactability`: website, phone, email, and contact form produce one bounded channel score. Contact confidence adjusts that score as a reliability multiplier rather than adding another set of contact points.
- `reputation`: rating and review-count evidence form one bounded supporting signal.

Signal weights, status values, distance bands, channel strengths, reputation normalization, inactive cap, and the minimum suitable score are configured under `providers` in `config/scoring.yaml`.

DataForSEO reports `work_time.work_hours.current_status` using `open`, `close`,
`temporarily_closed`, or `closed_forever`. The adapter normalizes its `close` value to
`closed_now`: the listing is outside its current opening hours, not marked as an inactive
business. `closed_now` therefore remains operating evidence, while temporary and permanent
closures receive no status credit. A raw, unnormalized `close` value is not configured for
full credit. See the [DataForSEO Business Listings response schema](https://docs.dataforseo.com/v3/business_data-business_listings-search-live/).

Provider records persist primary/additional categories, listing/service categories, coordinates, source timestamp, the normalized signal evidence, and each weighted contribution. DataForSEO's requested market is not copied into `service_area`; that field is used only when the provider source supplies service-area evidence.

The score is used to distinguish healthy provider supply from unusable, unreachable, closed, or excessive provider lists. Preferred-range and oversupply adjustments use only `suitable_provider_count`; irrelevant, inactive, geographically unsuitable, and low-scoring records remain visible in the raw count but cannot trigger saturation. It does not contact providers and does not imply a provider has agreed to buy leads.
