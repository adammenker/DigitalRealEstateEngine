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

`PublicDataAdapter` is the common source protocol. Named ACS, CBP, NES, NOAA, and FEMA
protocols make the intended adapters explicit. `OfflineFixtureAdapter` reads deterministic
JSON or JSONL exports; future download adapters should normalize source files into the same
contract.

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
retrieval time, adapter, granularity, refresh cadence, record count, the acquired source-file
SHA-256, and the normalized-record SHA-256. Release directories are immutable.

The lifecycle is:

1. **Stage** normalized records into a temporary directory.
2. **Validate** schema, unique identities, count, and checksum.
3. **Publish** the immutable release directory with an atomic rename.
4. **Activate** it through an atomic `registry.json` replacement.
5. **Roll back** by activating the previous validated release in activation history.

Activation never mutates records. Checksum drift blocks validation and activation.

## Refresh Commands

Run from the repository root with the project environment active:

```bash
PYTHONPATH=src python scripts/public_data.py refresh \
  --dataset cbp \
  --version 2022-v1 \
  --data-year 2022 \
  --release-date 2024-06-27 \
  --source /path/to/normalized-cbp.jsonl
```

Other operations:

```bash
PYTHONPATH=src python scripts/public_data.py status
PYTHONPATH=src python scripts/public_data.py validate --dataset cbp --version 2022-v1
PYTHONPATH=src python scripts/public_data.py activate --dataset cbp --version 2022-v1
PYTHONPATH=src python scripts/public_data.py rollback --dataset cbp
```

`refresh` is stage plus activate. A source release that already exists cannot be overwritten;
use a new version. Production automation should preserve the normalized source export beside
its acquisition log so a refresh can be reproduced from the same bytes.

## Data Age

Each source has a configured warning age. Snapshots compare the active release date with the
assessment date and attach warnings when the threshold is exceeded. Warnings reduce
assessment confidence; they do not silently alter values.

## Source Adapter Follow-Up

This workstream provides source interfaces, offline fixtures, durable caching, and refresh
operations. Production download adapters still need source-specific parsing and acquisition
credentials where applicable. NOAA and FEMA remain disabled until a service profile has a
reviewed causal use for them.
