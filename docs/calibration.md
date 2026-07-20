# Calibration

## Offline benchmark calibration

The calibration harness protects the discovery score's business direction before paid
production scanning. It is synthetic underwriting evidence, not a profitability model and
not a substitute for future outcome calibration.

### What runs

`config/benchmarks/manifest.yaml` pins the suite, active scoring snapshot, and all fixture
libraries. Suite `2026.07.1` contains:

- 26 versioned opportunity scenarios covering demand, competition, SERP displacement,
  provider supply, evidence freshness, evidence-gate failure, and preliminary assessments.
- 12 pairwise business-direction assertions.
- 12 labeled SERP classification cases, including ambiguous results.
- 11 labeled provider cases and 4 provider pairwise assertions.

The runner sends the fixture evidence through the production `ServiceCatalog`,
`classify_result`, `enrich_competitors`, `score_provider_suitability`,
`EvidenceQualityEvaluator`, and `OpportunityScorer` paths. Relative freshness values are
resolved against run time so a fresh scenario does not become stale merely because the
fixture is old.

Socket connections are denied for the complete run and comparison. A network attempt raises
an error instead of falling through to DataForSEO or another provider. Reports record the
attempt count, which must remain zero.

### Commands

```bash
rank-rent calibrate validate-config
rank-rent calibrate run
rank-rent calibrate report
rank-rent calibrate compare v2.12 v2.12
```

`calibrate run` writes an immutable JSON artifact to `benchmarks/reports` by default. Use
`--no-save` for CI, `--format json` for machine-readable stdout, or `--output-dir PATH` for a
different archive. `calibrate report [PATH]` reads an artifact without rerunning calibration;
without a path it selects the latest archived calibration report.

`calibrate compare A B` reruns the same offline corpus under both scoring snapshots and
reports score, rankability, and component deltas. Both versions must be registered in the
manifest. Passing the same version is a useful determinism and zero-network smoke test.

The Make target `calibration` validates configuration and runs the suite without writing an
artifact. CI runs the same two commands before pytest.

### Version discipline

1. Change configuration instead of adding exceptions for a named scenario.
2. Increment `config/scoring.yaml`'s version for every scoring behavior change.
3. Copy the complete configuration to `config/benchmarks/scoring/<version>.yaml`.
4. Register the snapshot in `config/benchmarks/manifest.yaml`.
5. Add or adjust directional scenarios and document the business rationale in the change.
6. Run `rank-rent calibrate validate-config`; it rejects a default snapshot that differs from
   the active scoring config, weights that do not total 100, or sub-signal shares that do not
   total 1.
7. Run and review `rank-rent calibrate compare OLD NEW`.
8. Preserve the generated JSON report. Historical artifacts are never overwritten.

The benchmark hash covers the manifest, all scenario and label libraries, the service
catalog, classifier and evidence-quality configuration, the active scoring config, and every
registered scoring snapshot. The scorer's own hash is recorded separately.

### Reading a benchmark report

The JSON report includes scenario checks, failed pairwise expectations, component
distributions, expected-versus-observed rankability counts, classification confusion,
provider signal results, scoring identity, suite identity, and configuration hashes.

A passing synthetic suite proves directional invariants only. It does not establish expected
revenue, lead volume, ranking time, or return on investment. Those require observed outcomes
linked to the exact historical score and evidence.

## Outcome feedback calibration

### Historical decision record

Before outcomes are imported, `PropertyOutcomeService.record_decision()` pins:

- Property integration ID
- Original opportunity
- Original `FullOpportunityScore`
- Exact scoring version
- Exact evidence artifact
- Selection date
- Service family, market-size band, and evidence quality
- Validated-opportunity acquisition cost
- Component scores present in the original full-score payload

The opportunity, score, and evidence artifact must agree. A second request with
the same property is idempotent only when those immutable references match.
Rescoring an opportunity does not alter the property decision.

### Outcome ingestion

`OutcomeSourceAdapter` returns typed daily records. Included fixture adapters
perform no network calls. Each record has a source type, source name, source
record ID, truth basis, confidence, and nonnegative metrics. The unique source
identity makes imports idempotent.

Supported source types cover Search Console, web analytics, call tracking,
forms, providers, and operators. Provider-reported and operator-verified facts
must use their corresponding source types. Reported revenue cannot be
estimated.

### Outcome reports

`CalibrationReportService` persists a versioned descriptive report containing:

- Selection score versus indexing time
- Selection score versus impression growth
- Selection score versus top-10 achievement
- Each captured score component versus qualified-lead volume
- Provider suitability versus won jobs
- Addressable-market score versus qualified-lead demand
- Segments by service family, market size, and evidence quality
- Cost per property that produced at least one qualified lead
- Separate observed and provider-reported totals

Pearson coefficients are emitted only as descriptive correlations. Every
comparison includes its sample size and a configured sufficiency flag. Reports
always state that correlation does not establish causation and
`scoring_changes_applied` is always false.

### Scoring guardrail

`ScoringChangeGuard` cannot write scoring configuration. It only records that a
human-initiated, version-changing proposal has a passing benchmark and named
reviewer. System-, report-, or automatically initiated proposals are rejected.
The stored review explicitly records `applied_automatically = false`.

Applying a reviewed scoring change remains a separate versioned configuration
workflow with benchmark comparison, reviewer approval, and rollback.
