from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import RawApiResponseORM


def cache_key(provider: str, endpoint: str, params: dict[str, Any], api_version: str) -> str:
    normalized = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    raw = f"{provider}:{endpoint}:{api_version}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


class RawResponseCache:
    def __init__(self, session: Session, provider: str, api_version: str = "fixture") -> None:
        self.session = session
        self.provider = provider
        self.api_version = api_version

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        key = cache_key(self.provider, endpoint, params, self.api_version)
        row = self.session.scalar(select(RawApiResponseORM).where(RawApiResponseORM.cache_key == key))
        return row.response_json if row else None

    def set(
        self,
        endpoint: str,
        params: dict[str, Any],
        response: dict[str, Any],
        *,
        status_code: int = 200,
        cost_usd: float = 0,
        provider_task_id: str | None = None,
    ) -> str:
        key = cache_key(self.provider, endpoint, params, self.api_version)
        if self.get(endpoint, params) is not None:
            return key
        now = datetime.now(UTC)
        self.session.add(
            RawApiResponseORM(
                cache_key=key,
                provider=self.provider,
                endpoint=endpoint,
                parameters=params,
                api_version=self.api_version,
                response_json=response,
                status_code=status_code,
                request_time=now,
                response_time=now,
                cost_usd=cost_usd,
                provider_task_id=provider_task_id,
            )
        )
        self.session.flush()
        return key

