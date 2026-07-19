# Demand Model

Demand evidence is produced locally from stored keyword metrics.

The model records raw provider volume by granularity, national service demand when provider data is country-level, provider-reported local demand, high-intent keyword counts, clustered demand, seasonality, and demand sources.

Local demand is only estimated when there is transparent evidence:

- Provider-local keyword volume is treated as directional local demand.
- Country-level volume can be converted by population share only when market and reference population metadata exists.
- Otherwise the report leaves local demand unestimated rather than inventing precision.

The demand score has two configured sub-signals:

- Service attractiveness uses national demand when it exists, otherwise provider-local demand.
- Market attractiveness uses measured local demand or the explicitly labeled population-share estimate.

National-only evidence therefore earns only the service-attractiveness share. It cannot receive market-attractiveness points until local demand is measured or estimated, and missing local demand caps overall confidence at `low`. Population-derived estimates are labeled `low` confidence and cap overall confidence at `medium`.

This keeps sandbox, fixture, replay, and production scans comparable without hiding weak geography evidence.
