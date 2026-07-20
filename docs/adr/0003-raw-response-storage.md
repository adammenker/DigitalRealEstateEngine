# ADR 0003: Raw Response Storage

Status: Accepted

Store large immutable provider responses and replay bundles through a `BlobStore`
interface. Local development uses a checksum-verifying filesystem adapter. Production
uses an S3-compatible versioned bucket with encryption. PostgreSQL stores object key,
checksum, type, size, provider lineage, retention classification, and encryption state.

Raw paid evidence must survive a transactional database reset and must never be edited
in place.

