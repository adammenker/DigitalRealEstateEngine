from __future__ import annotations

from typing import Any

from rank_rent.integrations.dataforseo.live import (
    DataForSEOLiveProvider,
    dataforseo_provider_name,
    normalize_dataforseo_environment,
)
from rank_rent.replay import ReplayMissError, ReplayTransport
from rank_rent.services.cache import normalize_request
from rank_rent.settings import Settings, get_settings


class DataForSEOReplayProvider(DataForSEOLiveProvider):
    provider_name = "dataforseo-replay"

    def __init__(
        self,
        transport: ReplayTransport,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout_seconds = 0
        self.cache = None
        self.force_refresh = False
        self.transport = transport
        self.api_environment = normalize_dataforseo_environment(self.settings)
        self.current_scan_run_id: int | None = None

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._get_stored_response(path, {})

    async def _post(self, path: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._get_stored_response(path, normalize_request({"tasks": tasks}))

    async def _get_stored_response(
        self,
        path: str,
        normalized_request: dict[str, Any],
    ) -> dict[str, Any]:
        first_error: ReplayMissError | None = None
        for provider in self._source_provider_names():
            try:
                stored = await self.transport.get_response(
                    provider,
                    path,
                    normalized_request,
                    self.api_version,
                )
                return stored.response_body
            except ReplayMissError as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error
        raise ReplayMissError(f"No stored response for {path}.")

    def _source_provider_names(self) -> list[str]:
        preferred = dataforseo_provider_name(self.settings)
        names = [preferred, "dataforseo-live", "dataforseo-sandbox"]
        deduped: list[str] = []
        for name in names:
            if name not in deduped:
                deduped.append(name)
        return deduped
