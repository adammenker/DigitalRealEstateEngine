# Discovery Exit Criteria

An opportunity discovery slice is healthy when:

- Fixture and replay scans run without network calls.
- Live testing scans default to DataForSEO sandbox unless production is explicitly configured.
- Every scan exposes planned calls, actual API call ledger rows, typed records, and a discovery report.
- Scores can be recomputed from stored evidence without provider calls.
- Demand granularity and missing data are visible in the report.
- SERP classifications include confidence and matched rules.
- Provider suitability is present for every saved provider candidate.
- `make verify` passes before production credits are used.
