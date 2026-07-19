# SERP Classification

SERP result classification is configured in `config/serp_classification.yaml`.

Supported labels:

- `local_provider`
- `directory`
- `national_brand`
- `marketplace`
- `lead_generator`
- `informational_publisher`
- `government_or_nonprofit`
- `unknown`

Each result stores classifier version, confidence, matched rule IDs, and evidence. Manual override fields exist in the model and database, but no operator override UI has been added yet.

Local-provider classification is intentionally conservative. A generic result containing words like
`repair`, `service`, or `contractor` is not enough. The result must show service relevance,
market/location relevance, and either a provider-listing match or clear business-identity evidence.
Ambiguous results remain `unknown`.

Classification labels are preserved as distinct competitor archetypes during enrichment.
Directories, marketplaces, lead generators, national service brands, local providers, and
informational publishers are not collapsed into a shared aggregator signal. Scoring assigns
each archetype its own configurable competitive adjustment.
