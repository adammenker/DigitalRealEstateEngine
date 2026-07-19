# Demand Model

Demand evidence is produced locally from stored keyword metrics.

The model records raw provider volume, metric granularity, national service demand when provider data is country-level, high-intent keyword counts, clustered demand, seasonality, and demand sources.

Local demand is only estimated when there is transparent evidence:

- Provider-local keyword volume is treated as directional local demand.
- Country-level volume can be converted by population share only when market and reference population metadata exists.
- Otherwise the report leaves local demand unestimated rather than inventing precision.

This keeps sandbox, fixture, replay, and production scans comparable without hiding weak evidence.
