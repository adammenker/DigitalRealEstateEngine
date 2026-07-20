# Corrupt Geography or Public Dataset

1. Stop prefilter runs and scans that depend on the affected dataset.
2. Verify manifest checksums, provenance, row counts, and active version.
3. Atomically activate the last known-good immutable dataset version.
4. Run location, prefilter, calibration, fixture, and replay tests.
5. Rebuild the candidate dataset in staging; never edit an active dataset in place.

