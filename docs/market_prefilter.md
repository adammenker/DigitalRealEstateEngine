# Addressable-Market Prefilter V2

The former market-prefilter result is now `AddressableMarketAssessment`. The new name makes
its scope explicit: it measures whether a market has a plausible addressable property and
provider base. It does not measure SEO opportunity.

Compatibility aliases retain `MarketPrefilter`, `MarketPrefilterAssessment`,
`MarketPrefilterProfile`, and `MarketPrefilterConfig` for existing API and scanner imports.

## Pipeline Position

```text
service x canonical U.S. markets
-> zero-cost AddressableMarketAssessment batch
-> selected candidates
-> paid DataForSEO testing scans
-> finalists
-> full scans
```

Addressable-market points never feed the SEO-opportunity score. A recommendation means
`advance_to_testing`, `review`, `defer`, or `insufficient_evidence`; it is a shortlist aid,
not a profitability claim.

## Service Profiles

`config/addressable_market/profiles.yaml` has a versioned profile for every enabled service
family plus a generic fallback. Profiles differ in evidence selection, weights, strong-value
thresholds, ideal supply ranges, and required coverage.

Every signal declares:

- source dataset and measure;
- causal rationale;
- expected direction;
- missing-data treatment;
- geographic granularity;
- refresh cadence;
- normalization and maximum credit.

Higher-is-better signals use a documented linear or log ceiling. Provider supply uses an
ideal range with tapered credit on both sides; lower supply is not assumed to be universally
better. Missing signals receive zero points and reduce evidence coverage. When coverage is
below the profile minimum, `score` is null and the result is `insufficient_evidence`.

Each assessment includes component points, raw values, source versions, data years, release
dates, limitations, missing signals, config hash, geography version, confidence, and
data-age warnings.

## Provider Density

The reviewed mapping registry is `config/addressable_market/naics.yaml`. It covers every
enabled service family and distinguishes:

- `exact`;
- `broad_parent`;
- `adjacent`.

Each mapping also has high, medium, or low confidence, review date, NAICS version, title, and
notes. Broad and adjacent records are discounted before scoring. The assessment exposes both
raw industry counts and weighted estimates, and always labels them as industry evidence rather
than exact provider or tenant counts.

The derived evidence is:

```text
weighted employer establishments / target households x 10,000
weighted nonemployer businesses / target households x 10,000
combined supply density
combined supply band
mapping/data confidence
```

Target households are profile-specific: usually owner-occupied units for residential property
services and all households where renter-occupied demand is also plausible.

## Batch APIs

Application code can call:

- `AddressableMarketPrefilter.assess_batch(service, records, limit=...)`;
- `AddressableMarketPrefilter.assess_geography_ids(service, ids, limit=...)`;
- `AddressableMarketPrefilter.rank_markets(...)`.

`AddressableMarketBatch` explicitly returns `zero_cost: true` and `paid_api_calls: 0`. The
module imports no DataForSEO provider, and tests patch HTTP clients to fail if any network
request is attempted.

Standalone commands provide the requested workflow without changing the central CLI:

```bash
PYTHONPATH=src python scripts/addressable_market.py top \
  --service roofing --state MO --limit 100

PYTHONPATH=src python scripts/addressable_market.py batch \
  --service roofing --markets /path/to/geography-ids.json
```

The market file can be a JSON string list or newline-delimited canonical geography IDs.

## Integration Note

The existing `/api/market-prefilter` endpoint automatically receives the V2 assessment
through compatibility aliases. Its route and persistence schema retain the older naming.

The exact master-spec commands:

```text
rank-rent prefilter batch
rank-rent prefilter top
```

still require thin command registration in `src/rank_rent/cli.py`. That file was intentionally
left untouched during the original focused workstream. This remains a small current code backlog
item. The standalone script and service APIs contain all business logic needed for that wiring.

The frontend can continue consuming existing fields while progressively adopting
`assessment_type`, `service_family_id`, `profile_version`, `evidence`, `provider_density`,
`dataset_versions`, and `data_age_warnings`.
