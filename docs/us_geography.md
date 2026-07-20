# Offline U.S. Geography

The discovery engine resolves every U.S. city or ZIP against
`data/us_geography.sqlite3` before it builds a live scan plan. Runtime location search does
not call a geocoder or DataForSEO.

## Contents

Each indexed geography contains:

- canonical city and state;
- ZIP codes and alternate place names;
- primary county and county FIPS;
- Core Based Statistical Area name and code, or an explicit nonmetropolitan label;
- Census internal-point latitude and longitude;
- ACS market population, households, housing units, owner-occupied units, median year built,
  and U.S. reference population;
- a bounded, area-equivalent provider-search radius;
- source GEOID and dataset version.

The checked-in `us-geography-2024.2` database contains more than 31,000 populated Census
places and 33,000 populated ZIP Code Tabulation Areas. Exact counts, source URLs, source
checksums, build time, and reference year are stored in the database `metadata` table.

## Runtime Rules

- The production discovery scope is U.S. cities and populated ZCTAs.
- An exact city/state or ZIP can resolve automatically.
- A city name shared by multiple states requires an explicit dropdown selection.
- Prefix and fuzzy matches are suggestions only; the user must select one.
- A selected candidate is reloaded by canonical geography ID. Client-supplied coordinates,
  population, and radius are never trusted.
- Live planning verifies the persisted fields against the index.
- Organic SERP requests use the canonical coordinate and convert the market boundary from
  kilometers to DataForSEO's meter-based coordinate radius.
- Provider discovery cannot run without verified coordinates and a positive radius.
- Production Business Listings requests use the canonical kilometer radius. Sandbox
  requests omit that unsupported mock filter while retaining sandbox evidence labels.
- Unsupported countries, unknown ZIPs, and unpopulated/non-geographic ZIPs are rejected.

ZCTAs are Census statistical approximations of ZIP service areas. They are not a complete
USPS delivery-route or PO-box database, so some valid mailing ZIPs are intentionally outside
the discovery scope.

## Sources And Attribution

The builder combines:

- U.S. Census Bureau 2024 Gazetteer place and ZCTA files;
- 2024 ACS five-year tables B01003, B11001, B25001, B25003, and B25035 for
  population and housing-market evidence;
- Census place/county and ZCTA/county/place relationship files;
- Census/OMB Core Based Statistical Area county delineation files;
- GeoNames U.S. postal data for alternate city names.

U.S. Census data is public domain. GeoNames data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/); this
project attributes GeoNames here and records the exact downloaded source in the database
manifest.

## Refresh

Install development dependencies and rebuild:

```bash
python3 -m pip install -e '.[dev]'
python3 scripts/build_us_geography.py \
  --source-dir .cache/us-geography \
  --output data/us_geography.sqlite3
```

The builder downloads missing sources, reuses existing source files, writes through a
temporary database, records source checksums in its manifest, and atomically replaces the
output.

## Public-Data Assessment Boundary

The canonical geography fields are the required foundation for market-demand estimation.
The opportunity demand estimator still uses only aligned market and U.S. reference
population. Household and housing fields feed the separate zero-cost market prefilter; they
are not silently converted into local Google search volume. Future dataset versions may add
climate or service-specific fields, but absent fields must not be inferred or treated as zero.
