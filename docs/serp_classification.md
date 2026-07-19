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
