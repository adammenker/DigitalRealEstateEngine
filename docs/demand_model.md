# Demand Model

Demand evidence is produced locally from stored keyword metrics.

The model records raw provider volume by granularity, national service demand when provider data is country-level, provider-reported local demand, high-intent keyword counts, clustered demand, seasonality, and demand sources.

Local demand is only estimated when there is transparent evidence:

- Provider-local keyword volume is treated as directional local demand.
- Country-level volume can be converted by population share only when market and reference population metadata exists.
- Otherwise the report leaves local demand unestimated rather than inventing precision.

Market estimation is selected by `demand.market_estimator` in `config/scoring.yaml` and
implemented behind a replaceable estimator interface. The current
`population_share_v2` strategy preserves a factor-level trace containing national service
demand, market population, reference population, and the calculated population share.
Its output remains `low` confidence.

The demand score has two configured sub-signals:

- Service attractiveness uses national demand when it exists and receives 65% of the
  demand-component point budget.
- Market attractiveness uses measured local demand or the explicitly labeled
  population-share estimate and receives 35% of the point budget.

National-only evidence therefore earns only the service-attractiveness share. It cannot receive market-attractiveness points until local demand is measured or estimated, and missing local demand caps overall confidence at `low`. Population-derived estimates are labeled `low` confidence and cap overall confidence at `medium`.

Market-attractiveness thresholds are calibrated by evidence type. Measured local demand
reaches full strength at 50 monthly searches. Population-estimated demand reaches its
evidence-type ceiling at 15 estimated monthly searches, but that ceiling is limited to 40%
of the market-attractiveness allocation. Population estimates can therefore contribute no
more than 3.36 of 24 demand points. This lets them differentiate markets on their natural
scale without allowing a crude population proxy to control opportunity ranking.

This keeps sandbox, fixture, replay, and production scans comparable without hiding weak geography evidence.

The current estimator deliberately does not use housing units, households, homeownership,
housing age, climate, or service-specific market factors. Those inputs should be added to
the versioned offline geography build and validated before a new estimator strategy uses
them. Adding a strategy does not require changing scan orchestration or the demand scoring
contract.
