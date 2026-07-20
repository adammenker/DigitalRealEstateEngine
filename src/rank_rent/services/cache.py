from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy.orm import Session

from rank_rent.db.orm import RawApiResponseORM, RawResponseCacheEntryORM
from rank_rent.settings import get_settings
from rank_rent.storage.blobs import BlobStore, build_blob_store

SENSITIVE_KEYS = {
    "authorization",
    "auth",
    "api_key",
    "api_token",
    "password",
    "login",
    "dataforseo_login",
    "dataforseo_password",
}

DEFAULT_RESPONSE_SHAPE_VERSION = "v1"
RAW_RESPONSE_CONTENT_TYPE = "application/json"


class RawResponseIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CacheLookup:
    cache_key: str
    row: RawApiResponseORM | None
    payload: dict[str, Any] | None
    invalid_reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.row is not None and self.payload is not None and self.invalid_reason is None


def normalize_request(params: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return cast(dict[str, Any], json.loads(encoded))


def cache_key(
    provider: str,
    endpoint: str,
    params: dict[str, Any],
    api_version: str,
    response_shape_version: str = DEFAULT_RESPONSE_SHAPE_VERSION,
) -> str:
    normalized = json.dumps(normalize_request(params), sort_keys=True, separators=(",", ":"))
    raw = f"{provider}:{endpoint}:{api_version}:{response_shape_version}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


def checksum_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(serialize_payload(payload)).hexdigest()


def serialize_payload(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return encoded.encode()


def raw_response_payload(
    row: RawApiResponseORM,
    blob_store: BlobStore | None = None,
) -> dict[str, Any]:
    if row.object_key is None:
        payload = row.response_json
    else:
        store = blob_store or build_blob_store(get_settings())
        try:
            encoded = store.get(row.object_key)
        except Exception as exc:
            raise RawResponseIntegrityError(
                f"Raw response blob {row.object_key!r} is unavailable."
            ) from exc
        if row.size_bytes is not None and len(encoded) != row.size_bytes:
            raise RawResponseIntegrityError(
                f"Raw response blob size mismatch for {row.object_key!r}."
            )
        try:
            decoded = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RawResponseIntegrityError(
                f"Raw response blob {row.object_key!r} is not valid JSON."
            ) from exc
        if not isinstance(decoded, dict):
            raise RawResponseIntegrityError(
                f"Raw response blob {row.object_key!r} must contain a JSON object."
            )
        payload = cast(dict[str, Any], decoded)
    if row.checksum and row.checksum != checksum_payload(payload):
        raise RawResponseIntegrityError(
            f"Raw response checksum mismatch for {row.provider} {row.endpoint}."
        )
    return payload


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def ttl_for_endpoint(endpoint: str) -> timedelta | None:
    if "/locations/" in endpoint:
        return timedelta(days=90)
    if "keyword_suggestions" in endpoint:
        return timedelta(days=30)
    if "historical_search_volume" in endpoint:
        return timedelta(days=30)
    if "business_listings" in endpoint:
        return timedelta(days=10)
    if "backlinks" in endpoint:
        return timedelta(days=45)
    if "/serp/" in endpoint:
        return None
    return timedelta(days=30)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def current_raw_response(
    session: Session,
    key: str,
) -> RawApiResponseORM | None:
    pointer = session.get(RawResponseCacheEntryORM, key)
    if pointer is None:
        return None
    row = session.get(RawApiResponseORM, pointer.raw_api_response_id)
    if row is None or row.cache_key != key:
        return None
    return row


def lookup_cached_response(
    session: Session,
    key: str,
    *,
    blob_store: BlobStore | None = None,
    now: datetime | None = None,
) -> CacheLookup:
    pointer = session.get(RawResponseCacheEntryORM, key)
    if pointer is None:
        return CacheLookup(key, None, None, "missing_cache_pointer")
    row = session.get(RawApiResponseORM, pointer.raw_api_response_id)
    if row is None:
        return CacheLookup(key, None, None, "missing_response_metadata")
    if row.cache_key != key:
        return CacheLookup(key, row, None, "cache_key_mismatch")
    expected_key = cache_key(
        row.provider,
        row.endpoint,
        normalize_request(row.parameters),
        row.api_version,
        row.response_shape_version,
    )
    if expected_key != key:
        return CacheLookup(key, row, None, "response_metadata_mismatch")
    if not row.sanitized:
        return CacheLookup(key, row, None, "unsanitized_response")
    if not row.checksum:
        return CacheLookup(key, row, None, "missing_checksum")
    if row.object_key is None and not isinstance(row.response_json, dict):
        return CacheLookup(key, row, None, "invalid_inline_response_metadata")
    current_time = now or datetime.now(UTC)
    if row.expires_at is not None and _as_aware_utc(row.expires_at) <= current_time:
        return CacheLookup(key, row, None, "expired")
    if row.object_key is not None and (
        not row.storage_backend
        or row.content_type != RAW_RESPONSE_CONTENT_TYPE
        or row.size_bytes is None
        or row.blob_created_at is None
    ):
        return CacheLookup(key, row, None, "incomplete_blob_metadata")
    try:
        payload = raw_response_payload(row, blob_store)
    except RawResponseIntegrityError:
        return CacheLookup(key, row, None, "response_integrity_failure")
    return CacheLookup(key, row, payload)


def valid_cached_response(
    session: Session,
    key: str,
    *,
    blob_store: BlobStore | None = None,
    now: datetime | None = None,
) -> bool:
    return lookup_cached_response(session, key, blob_store=blob_store, now=now).is_valid


class RawResponseCache:
    def __init__(
        self,
        session: Session,
        provider: str,
        api_version: str = "fixture",
        response_shape_version: str = DEFAULT_RESPONSE_SHAPE_VERSION,
        blob_store: BlobStore | None = None,
        storage_backend: str | None = None,
        encryption_status: str = "not_encrypted",
    ) -> None:
        self.session = session
        self.provider = provider
        self.api_version = api_version
        self.response_shape_version = response_shape_version
        self.blob_store = blob_store
        self.storage_backend = storage_backend
        self.encryption_status = encryption_status

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        return self.lookup(endpoint, params).payload

    def get_row(self, endpoint: str, params: dict[str, Any]) -> RawApiResponseORM | None:
        return current_raw_response(self.session, self._cache_key(endpoint, params))

    def lookup(self, endpoint: str, params: dict[str, Any]) -> CacheLookup:
        return lookup_cached_response(
            self.session,
            self._cache_key(endpoint, params),
            blob_store=self.blob_store,
        )

    def _cache_key(self, endpoint: str, params: dict[str, Any]) -> str:
        return cache_key(
            self.provider,
            endpoint,
            normalize_request(params),
            self.api_version,
            self.response_shape_version,
        )

    def set(
        self,
        endpoint: str,
        params: dict[str, Any],
        response: dict[str, Any],
        *,
        status_code: int = 200,
        cost_usd: float = 0,
        provider_task_id: str | None = None,
        provider_request_id: str | None = None,
        source_scan_run_id: int | None = None,
    ) -> str:
        normalized = normalize_request(params)
        key = self._cache_key(endpoint, normalized)
        now = datetime.now(UTC)
        ttl = ttl_for_endpoint(endpoint)
        sanitized_response = cast(dict[str, Any], sanitize_payload(response))
        checksum = checksum_payload(sanitized_response)
        object_key: str | None = None
        content_type: str | None = None
        size_bytes: int | None = None
        blob_created_at: datetime | None = None
        response_json = sanitized_response
        if self.blob_store is not None:
            object_key = (
                f"raw-responses/{self.provider}/{checksum[:2]}/{checksum}.json"
            )
            blob = self.blob_store.put(
                object_key,
                serialize_payload(sanitized_response),
                content_type=RAW_RESPONSE_CONTENT_TYPE,
            )
            if blob.checksum != checksum:
                raise RawResponseIntegrityError(
                    f"Blob store checksum mismatch while writing {object_key!r}."
                )
            content_type = blob.content_type
            size_bytes = blob.size_bytes
            blob_created_at = now
            response_json = {}
        row = RawApiResponseORM(
            cache_key=key,
            provider=self.provider,
            endpoint=endpoint,
            parameters=normalized,
            api_version=self.api_version,
            response_shape_version=self.response_shape_version,
            response_json=response_json,
            sanitized=True,
            status_code=status_code,
            request_time=now,
            response_time=now,
            cost_usd=cost_usd,
            provider_task_id=provider_task_id,
            provider_request_id=provider_request_id,
            source_scan_run_id=source_scan_run_id,
            checksum=checksum,
            expires_at=now + ttl if ttl else None,
            object_key=object_key,
            storage_backend=self.storage_backend if object_key else None,
            content_type=content_type,
            size_bytes=size_bytes,
            retention_classification="raw_provider_response",
            encryption_status=self.encryption_status if object_key else "not_applicable",
            blob_created_at=blob_created_at,
        )
        self.session.add(row)
        self.session.flush()
        pointer = self.session.get(RawResponseCacheEntryORM, key)
        if pointer is None:
            self.session.add(
                RawResponseCacheEntryORM(cache_key=key, raw_api_response_id=row.id)
            )
        else:
            pointer.raw_api_response_id = row.id
        self.session.flush()
        return key
