# Evidence Quality

`config/evidence_quality.yaml` defines the versioned gate applied before an assessment can be
trusted.

The gate evaluates:

- keyword and representative-query service relevance;
- provider service and geographic relevance;
- minimum competitor coverage for full scans;
- unknown organic-result classification share;
- configured-service eligibility for full assessment.

Each assessment receives a `pass`, `warning`, or `fail` result with check-level measurements,
thresholds, and reasons. A failure marks the evidence unusable, caps the score, and prevents
ranking, comparison, and testing-to-full promotion. Warnings remain reviewable but cannot be
presented with high confidence.

Evidence quality is separate from attractiveness. Missing or noisy evidence should reduce
trust rather than be interpreted as proof that a market is either good or bad. The discovery
report therefore presents the gate alongside score components, source mode, missing evidence,
freshness, and the reconciled API-call ledger.
