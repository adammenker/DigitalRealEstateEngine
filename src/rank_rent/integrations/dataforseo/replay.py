from __future__ import annotations

from typing import Any

from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.replay import ReplayTransport
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

    async def _get(self, path: str) -> dict[str, Any]:
        stored = await self.transport.get_response(
            "dataforseo-live",
            path,
            {},
            self.api_version,
        )
        return stored.response_body

    async def _post(self, path: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        stored = await self.transport.get_response(
            "dataforseo-live",
            path,
            normalize_request({"tasks": tasks}),
            self.api_version,
        )
        return stored.response_body
