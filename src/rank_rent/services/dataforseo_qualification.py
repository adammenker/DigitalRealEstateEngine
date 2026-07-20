from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from rank_rent.db.orm import ProviderQualificationORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import (
    DataForSEOAuthenticationError,
    DataForSEOError,
    DataForSEOLiveProvider,
    DataForSEORateLimitError,
    DataForSEOSchemaError,
)
from rank_rent.services.qualification import (
    DATAFORSEO_ADAPTER_VERSION,
    execute_qualification,
)
from rank_rent.settings import Settings


class DataForSEOQualificationExecutor:
    def __init__(
        self,
        provider: DataForSEOLiveProvider,
        *,
        service: ServiceFamily,
        market: Market,
    ) -> None:
        self.provider = provider
        self.service = service
        self.market = market
        self._keywords: list[str] = []
        self._serp_urls: list[str] = []
        self._serp_features: list[str] | None = None

    async def execute_check(self, check_name: str) -> dict[str, Any]:
        handler = getattr(self, f"_check_{check_name}", None)
        if handler is None:
            raise RuntimeError(f"No executable qualification check exists for {check_name}.")
        result = await handler()
        return {"passed": True, **result}

    async def _check_account_access(self) -> dict[str, Any]:
        result = await self.provider.check_account()
        status_code = result.get("status_code")
        if not isinstance(status_code, int) or status_code >= 40000:
            raise DataForSEOError("Qualification account probe did not return a success status.")
        return {
            "source": "provider_probe",
            "status_code": status_code,
            "status_message": result.get("status_message"),
        }

    async def _check_location_lookup(self) -> dict[str, Any]:
        result = await self.provider.resolve_location(self.market.display_name)
        if not result.provider_location_code:
            raise DataForSEOSchemaError("Qualification location probe returned no location code.")
        return {
            "source": "provider_probe",
            "provider_location_code": result.provider_location_code,
            "provider_location_name": result.provider_location_name,
        }

    async def _check_keyword_suggestions(self) -> dict[str, Any]:
        keywords = await self.provider.discover_keywords(self.service, self.market)
        if not any(item.source.startswith("dataforseo:") for item in keywords):
            raise DataForSEOSchemaError(
                "Qualification received no provider-generated keyword suggestions."
            )
        self._keywords = [item.keyword for item in keywords]
        if not self._keywords:
            raise DataForSEOSchemaError("Qualification returned no keyword candidates.")
        return {"source": "provider_probe", "result_count": len(self._keywords)}

    async def _check_keyword_metrics(self) -> dict[str, Any]:
        if not self._keywords:
            await self._check_keyword_suggestions()
        metrics = await self.provider.get_keyword_metrics(self._keywords[:3], self.market)
        if not metrics:
            raise DataForSEOSchemaError("Qualification returned no keyword metrics.")
        return {"source": "provider_probe", "result_count": len(metrics)}

    async def _check_serps(self) -> dict[str, Any]:
        if not self._keywords:
            await self._check_keyword_suggestions()
        snapshot = await self.provider.get_serp_snapshot(self._keywords[0], self.market)
        if not snapshot.results:
            raise DataForSEOSchemaError("Qualification returned no organic SERP results.")
        self._serp_urls = [result.url for result in snapshot.results if result.url]
        self._serp_features = snapshot.features_present
        return {
            "source": "provider_probe",
            "organic_result_count": len(snapshot.results),
            "feature_count": len(snapshot.features_present),
        }

    async def _check_serp_features(self) -> dict[str, Any]:
        if self._serp_features is None:
            await self._check_serps()
        return {
            "source": "provider_probe",
            "schema_validated": True,
            "observed_features": self._serp_features or [],
        }

    async def _check_backlinks(self) -> dict[str, Any]:
        if not self._serp_urls:
            await self._check_serps()
        targets = self._serp_urls[:1] or ["https://example.com/"]
        metrics = await self.provider.get_competitor_metrics(targets)
        if not metrics:
            raise DataForSEOSchemaError("Qualification returned no backlink metrics.")
        return {"source": "provider_probe", "result_count": len(metrics)}

    async def _check_business_listings(self) -> dict[str, Any]:
        providers = await self.provider.find_providers(self.service, self.market)
        if not providers:
            raise DataForSEOSchemaError("Qualification returned no business listings.")
        return {"source": "provider_probe", "result_count": len(providers)}

    async def _check_partial_tasks(self) -> dict[str, Any]:
        self._expect_error(
            self._response(
                200,
                {
                    "status_code": 20000,
                    "tasks": [
                        {"status_code": 20000, "result": []},
                        {"status_code": 40501, "status_message": "task failed"},
                    ],
                },
            ),
            DataForSEOError,
        )
        return {"source": "adapter_contract_probe", "rejected_partial_failure": True}

    async def _check_rate_limits(self) -> dict[str, Any]:
        self._expect_error(self._response(429, {"tasks": []}), DataForSEORateLimitError)
        return {"source": "adapter_contract_probe", "recognized_http_status": 429}

    async def _check_billing_errors(self) -> dict[str, Any]:
        error = self._expect_error(
            self._response(402, {"status_message": "payment required", "tasks": []}),
            DataForSEOError,
        )
        if "billing" not in str(error).lower() and "balance" not in str(error).lower():
            raise AssertionError("Billing error was not classified with actionable context.")
        return {"source": "adapter_contract_probe", "recognized_http_status": 402}

    async def _check_authentication_errors(self) -> dict[str, Any]:
        self._expect_error(
            self._response(401, {"tasks": []}),
            DataForSEOAuthenticationError,
        )
        return {"source": "adapter_contract_probe", "recognized_http_status": 401}

    async def _check_schema_drift(self) -> dict[str, Any]:
        self._expect_error(
            self._response(200, ["unexpected", "response"]),
            DataForSEOSchemaError,
        )
        return {"source": "adapter_contract_probe", "rejected_non_object_json": True}

    def _expect_error(
        self,
        response: httpx.Response,
        error_type: type[Exception],
    ) -> Exception:
        try:
            self.provider._parse_response(response)
        except error_type as exc:
            return exc
        raise AssertionError(f"Adapter did not raise {error_type.__name__}.")

    @staticmethod
    def _response(status_code: int, payload: Any) -> httpx.Response:
        return httpx.Response(
            status_code,
            json=payload,
            request=httpx.Request("POST", "https://qualification.invalid/v3/probe"),
        )


async def run_dataforseo_qualification(
    session: Session,
    *,
    settings: Settings,
    service: ServiceFamily,
    market: Market,
    executed_by: str,
    notes: str = "",
) -> ProviderQualificationORM:
    provider = DataForSEOLiveProvider(
        settings=settings,
        session=session,
        allow_unplanned_requests=True,
        force_refresh=True,
    )
    executor = DataForSEOQualificationExecutor(
        provider,
        service=service,
        market=market,
    )
    return await execute_qualification(
        session,
        provider=provider.provider_name,
        environment=provider.api_environment,
        adapter_version=DATAFORSEO_ADAPTER_VERSION,
        executor=executor,
        ttl_hours=settings.qualification_ttl_hours,
        executed_by=executed_by,
        notes=notes,
    )
