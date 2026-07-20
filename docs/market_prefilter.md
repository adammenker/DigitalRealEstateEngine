# Public-Data Market Prefilter

The market prefilter is a separate, zero-cost assessment stage that runs before any
DataForSEO scan. It narrows thousands of canonical U.S. markets to a shortlist using
checked-in public evidence:

```text
service x canonical U.S. markets
-> ACS public-data prefilter
-> selected candidates
-> DataForSEO testing scans
-> finalists
-> full scans
```

## Evidence

The `us-geography-2024.2` index adds 2024 ACS five-year:

- households (`B11001`);
- housing units (`B25001`);
- owner-occupied units (`B25003`);
- median year built (`B25035`).

The index retains the exact source URLs, checksums, build timestamp, and dataset version.
No Census API call occurs at runtime.

## Assessment

`config/market_prefilter.yaml` contains versioned service-profile matching, signal weights,
normalization targets, population floor, result limit, and recommendation thresholds.
Home-service searches use household, housing-stock, ownership, and housing-age evidence.
Other local services use the generic population, household, housing-unit, and household-density
profile.

Each assessment stores its component points, raw inputs, weights, normalization targets,
missing fields, confidence, recommendation, config hash, and geography dataset version.
Prefilter runs and returned assessments are persisted in `market_prefilter_runs` and
`market_prefilter_assessments`.

The dashboard's **Find markets** command ranks city markets without paid calls. Selecting a
result fills the normal canonical location control. Dry runs and completed discovery reports
also include the selected market's prefilter assessment.

## Boundary

The prefilter measures addressable market structure. It does not claim to measure Google
demand, ranking difficulty, provider quality, or expected revenue, and it does not feed its
score into the opportunity score. A weak prefilter result does not automatically block a
manual scan.

County Business Patterns, Nonemployer Statistics, climate, and validated trend signals can
be added as independent modules after service-to-NAICS mappings and cross-market validation
are available. Until then, the assessment remains `medium` confidence at best.
