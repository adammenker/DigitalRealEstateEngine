from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import RawApiResponseORM, ScanRunORM
from rank_rent.services.cache import (
    DEFAULT_RESPONSE_SHAPE_VERSION,
    cache_key,
    checksum_payload,
    normalize_request,
)


class ReplayMissError(RuntimeError):
    pass


class StoredApiResponse(BaseModel):
    provider: str
    endpoint: str
    api_version: str
    response_shape_version: str = DEFAULT_RESPONSE_SHAPE_VERSION
    normalized_request: dict[str, Any]
    raw_response: dict[str, Any] = Field(validation_alias=AliasChoices("raw_response", "response_body"))
    sanitized: bool = True
    provider_task_id: str | None = None
    provider_request_id: str | None = None
    provider_cost_usd: Decimal | None = None
    requested_at: datetime
    received_at: datetime
    source_scan_run_id: int | None = None
    checksum: str

    @property
    def response_body(self) -> dict[str, Any]:
        return self.raw_response

    def validate_checksum(self) -> None:
        actual = checksum_payload(self.raw_response)
        if self.checksum != actual:
            raise ReplayIntegrityError(
                f"Stored response checksum mismatch for {self.provider} {self.endpoint}."
            )


class ReplayTransport(Protocol):
    async def get_response(
        self,
        provider: str,
        endpoint: str,
        normalized_request: dict[str, Any],
        api_version: str | None = None,
    ) -> StoredApiResponse: ...


def checksum_response(response_body: dict[str, Any]) -> str:
    encoded = json.dumps(response_body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ReplayIntegrityError(RuntimeError):
    pass


def stored_response_from_orm(
    row: RawApiResponseORM,
    *,
    source_scan_run_id: int | None = None,
) -> StoredApiResponse:
    normalized = normalize_request(row.parameters)
    return StoredApiResponse(
        provider=row.provider,
        endpoint=row.endpoint,
        api_version=row.api_version,
        response_shape_version=row.response_shape_version,
        normalized_request=normalized,
        raw_response=row.response_json,
        sanitized=row.sanitized,
        provider_task_id=row.provider_task_id,
        provider_request_id=row.provider_request_id,
        provider_cost_usd=Decimal(str(row.cost_usd)) if row.cost_usd is not None else None,
        requested_at=row.request_time,
        received_at=row.response_time,
        source_scan_run_id=source_scan_run_id,
        checksum=row.checksum or checksum_response(row.response_json),
    )


class DatabaseReplayTransport:
    def __init__(self, session: Session, api_version: str = "v3") -> None:
        self.session = session
        self.api_version = api_version

    async def get_response(
        self,
        provider: str,
        endpoint: str,
        normalized_request: dict[str, Any],
        api_version: str | None = None,
    ) -> StoredApiResponse:
        key = cache_key(provider, endpoint, normalized_request, api_version or self.api_version)
        row = self.session.scalar(select(RawApiResponseORM).where(RawApiResponseORM.cache_key == key))
        if row is None:
            raise ReplayMissError(
                f"No stored response for {provider} {endpoint}. Replay mode makes no network calls."
            )
        stored = stored_response_from_orm(row)
        stored.validate_checksum()
        return stored


class BundleReplayTransport:
    def __init__(self, responses: list[StoredApiResponse]) -> None:
        self.responses = {
            cache_key(
                item.provider,
                item.endpoint,
                item.normalized_request,
                item.api_version,
                item.response_shape_version,
            ): item
            for item in responses
        }
        for response in responses:
            response.validate_checksum()

    async def get_response(
        self,
        provider: str,
        endpoint: str,
        normalized_request: dict[str, Any],
        api_version: str | None = None,
    ) -> StoredApiResponse:
        key = cache_key(provider, endpoint, normalized_request, api_version or "v3")
        response = self.responses.get(key)
        if response is None:
            raise ReplayMissError(
                f"No bundled response for {provider} {endpoint}. Replay mode makes no network calls."
            )
        return response


def export_responses_for_scan(
    session: Session,
    output_path: str,
    *,
    scan_run_id: int | None = None,
) -> None:
    source_scan_run_id = None
    query = select(RawApiResponseORM).order_by(RawApiResponseORM.id)
    rows: Sequence[RawApiResponseORM]
    if scan_run_id is not None:
        scan = session.get(ScanRunORM, scan_run_id)
        if scan is None:
            raise ValueError(f"Scan run {scan_run_id} was not found.")
        source_scan_run_id = scan_run_id
        if scan.started_at is None:
            rows = []
        else:
            query = query.where(RawApiResponseORM.request_time >= scan.started_at)
            if scan.completed_at is not None:
                query = query.where(RawApiResponseORM.response_time <= scan.completed_at)
            rows = session.scalars(query).all()
    else:
        rows = session.scalars(query).all()
    responses = [
        stored_response_from_orm(row, source_scan_run_id=source_scan_run_id).model_dump(mode="json")
        for row in rows
    ]
    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "source_scan_run_id": scan_run_id,
        "responses": responses,
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def load_response_bundle(bundle_path: str) -> BundleReplayTransport:
    with open(bundle_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    responses = [StoredApiResponse.model_validate(item) for item in payload.get("responses", [])]
    return BundleReplayTransport(responses)


def validate_response_bundle(bundle_path: str) -> dict[str, Any]:
    with open(bundle_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    responses = [StoredApiResponse.model_validate(item) for item in payload.get("responses", [])]
    for response in responses:
        response.validate_checksum()
        if not response.sanitized:
            raise ReplayIntegrityError(
                f"Stored response for {response.provider} {response.endpoint} is not marked sanitized."
            )
    return {
        "response_count": len(responses),
        "source_scan_run_id": payload.get("source_scan_run_id"),
        "exported_at": payload.get("exported_at"),
    }
