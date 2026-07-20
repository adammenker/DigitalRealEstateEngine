# Public-Data Store

The public-data package provides reproducible, offline evidence for
`AddressableMarketAssessment`. It is intentionally independent of DataForSEO and the
SEO-opportunity score.

## Supported Sources

The source catalog is `config/public_data/datasets.yaml`.

| Dataset | Status | Purpose |
| --- | --- | --- |
| ACS | Required baseline | Households, housing stock, tenure, structure, and income |
| County Business Patterns | Optional | Employer-establishment density through reviewed NAICS mappings |
| Nonemployer Statistics | Optional | Nonemployer-business density through reviewed NAICS mappings |
| NOAA | Interface only, disabled | Service-specific severe-weather exposure |
| FEMA | Interface only, disabled | Explicitly relevant hazard exposure |

The existing geography database remains a compatible embedded ACS baseline. An activated
ACS release can overlay it with additional measures and complete release provenance.
CBP/NES are never inferred from ACS or provider search data.

## Normalized Record

Adapters emit `DatasetRecord` values:

```json
{
  "geography_id": "county:29510",
  "source_geoid": "29510",
  "geography_level": "county",
  "dimensions": {"naics_code": "238220"},
  "values": {"establishments": 40}
}
```

One identity is the tuple of canonical geography, source GEOID, and dimensions. Duplicate
identities fail staging. Values must be numeric, finite, or explicitly null.

`PublicDataAdapter` is the common source protocol. `ACSAdapter`, `CBPAdapter`, and
`NESAdapter` acquire and normalize the Census API array-JSON format and official-style CSV
downloads. Acquisition is behind `PublicDataHTTPTransport`: production uses the bounded
HTTPS implementation, while offline refreshes use `FilePublicDataTransport` against the
exact previously downloaded bytes. Tests inject transports and make no external requests.
CI also sets `PUBLIC_DATA_NETWORK_DISABLED=true`, which makes the default HTTP transport
fail before creating a client.

The adapters currently normalize:

- ACS 5-year place, county, and ZCTA records: population, households, housing units,
  owner-occupied units, median housing year, and median household income.
- CBP county records: NAICS, establishments, employees, and annual payroll.
- NES county records: NAICS, nonemployer establishments, and receipts.

Census suppression markers and documented negative estimate sentinels become `null`.
Malformed headers, row widths, FIPS values, NAICS values, or measurements fail the complete
refresh. NOAA and FEMA remain typed, disabled extension points until reviewed source and
service-specific normalization rules exist.

## Release Lifecycle

Runtime data lives under ignored `data/public_data/`:

```text
registry.json
datasets/
  cbp/
    2022-v1/
      manifest.json
      records.jsonl
```

Every release manifest records source name and URL, license, data year, release date,
retrieval time, adapter version, acquisition method, source format and byte count,
content type, ETag/last-modified metadata when available, granularity, refresh cadence,
record count, the acquired-source SHA-256, and the normalized-record SHA-256. Census API
keys are stripped from persisted URLs. Release directories are immutable.

The lifecycle is:

1. **Stage** normalized records into a temporary directory.
2. **Validate** schema, unique identities, count, and checksum.
3. **Publish** the immutable release directory with an atomic rename.
4. **Activate** it through a locked, atomic `registry.json` replacement.
5. **Roll back** by activating the previous validated release in activation history.

Activation never mutates records. Checksum drift blocks validation and activation. Registry
mutations use a token-owned filesystem lock with a timeout. A live local process's lock is
not stolen; an abandoned lock can be recovered after the stale threshold. Concurrent stages
and activations therefore preserve all versions and activation history.

If a process stops after installing a release directory but before registering it, retrying
the identical release validates the installed bytes and provenance and completes the
registration. Different bytes or provenance under the same version are rejected.

## Refresh Commands

Run from the repository root with the project environment active:

```bash
PYTHONPATH=src CENSUS_API_KEY=... python scripts/public_data.py refresh \
  --dataset acs \
  --version 2024-acs5 \
  --data-year 2024 \
  --release-date 2025-12-11
```

For a reproducible offline refresh from an official Census API response or CSV download:

```bash
PYTHONPATH=src python scripts/public_data.py refresh \
  --dataset cbp \
  --version 2023-cbp \
  --data-year 2023 \
  --release-date 2025-06-26 \
  --official-source /path/to/official-cbp.csv \
  --expected-sha256 <sha256>
```

The legacy normalized JSON/JSONL path remains available for reviewed NOAA/FEMA extracts and
development fixtures:

```bash
PYTHONPATH=src python scripts/public_data.py stage \
  --dataset noaa \
  --version reviewed-v1 \
  --data-year 2025 \
  --release-date 2026-01-01 \
  --source /path/to/normalized-noaa.jsonl
```

Other operations:

```bash
PYTHONPATH=src python scripts/public_data.py status
PYTHONPATH=src python scripts/public_data.py validate --dataset cbp --version 2022-v1
PYTHONPATH=src python scripts/public_data.py activate --dataset cbp --version 2022-v1
PYTHONPATH=src python scripts/public_data.py rollback --dataset cbp
```

`refresh` is stage plus activate. A registered source release cannot be overwritten; use a
new version. Preserve the original acquired file and expected checksum so a refresh can be
reproduced without network access.

## Data Age

Each source has a configured warning age. Snapshots compare the active release date with the
assessment date and attach warnings when the threshold is exceeded. Warnings reduce
assessment confidence; they do not silently alter values.

## Operational Limits

Census API access requires a Census key. Large nationwide CBP/NES refreshes are better
performed from the official downloadable file and staged offline. Source release dates and
NAICS vintages remain operator-reviewed metadata because the APIs do not provide a single
machine-readable release manifest with every response.
