from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import RawApiResponseORM

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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


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


class RawResponseCache:
    def __init__(
        self,
        session: Session,
        provider: str,
        api_version: str = "fixture",
        response_shape_version: str = DEFAULT_RESPONSE_SHAPE_VERSION,
    ) -> None:
        self.session = session
        self.provider = provider
        self.api_version = api_version
        self.response_shape_version = response_shape_version

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        row = self.get_row(endpoint, params)
        if row is None:
            return None
        if row.expires_at is not None and _as_aware_utc(row.expires_at) <= datetime.now(UTC):
            return None
        if row.checksum and row.checksum != checksum_payload(row.response_json):
            return None
        return row.response_json if row else None

    def get_row(self, endpoint: str, params: dict[str, Any]) -> RawApiResponseORM | None:
        key = cache_key(
            self.provider,
            endpoint,
            normalize_request(params),
            self.api_version,
            self.response_shape_version,
        )
        return self.session.scalar(select(RawApiResponseORM).where(RawApiResponseORM.cache_key == key))

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
        key = cache_key(
            self.provider,
            endpoint,
            normalized,
            self.api_version,
            self.response_shape_version,
        )
        if self.get(endpoint, params) is not None:
            return key
        now = datetime.now(UTC)
        ttl = ttl_for_endpoint(endpoint)
        sanitized_response = cast(dict[str, Any], sanitize_payload(response))
        self.session.add(
            RawApiResponseORM(
                cache_key=key,
                provider=self.provider,
                endpoint=endpoint,
                parameters=normalized,
                api_version=self.api_version,
                response_shape_version=self.response_shape_version,
                response_json=sanitized_response,
                sanitized=True,
                status_code=status_code,
                request_time=now,
                response_time=now,
                cost_usd=cost_usd,
                provider_task_id=provider_task_id,
                provider_request_id=provider_request_id,
                source_scan_run_id=source_scan_run_id,
                checksum=checksum_payload(sanitized_response),
                expires_at=now + ttl if ttl else None,
            )
        )
        self.session.flush()
        return key
