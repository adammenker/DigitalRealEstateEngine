from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordCandidate,
    KeywordMetric,
    LocationType,
    Market,
    ProviderCandidate,
    ResolvedLocation,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
    slugify,
)
from rank_rent.runtime import DataMode, validate_runtime_mode
from rank_rent.settings import Settings, get_settings


class DataForSEOError(RuntimeError):
    pass


STATE_NAMES = {
    "al": "alabama",
    "ak": "alaska",
    "az": "arizona",
    "ar": "arkansas",
    "ca": "california",
    "co": "colorado",
    "ct": "connecticut",
    "de": "delaware",
    "fl": "florida",
    "ga": "georgia",
    "hi": "hawaii",
    "id": "idaho",
    "il": "illinois",
    "in": "indiana",
    "ia": "iowa",
    "ks": "kansas",
    "ky": "kentucky",
    "la": "louisiana",
    "me": "maine",
    "md": "maryland",
    "ma": "massachusetts",
    "mi": "michigan",
    "mn": "minnesota",
    "ms": "mississippi",
    "mo": "missouri",
    "mt": "montana",
    "ne": "nebraska",
    "nv": "nevada",
    "nh": "new hampshire",
    "nj": "new jersey",
    "nm": "new mexico",
    "ny": "new york",
    "nc": "north carolina",
    "nd": "north dakota",
    "oh": "ohio",
    "ok": "oklahoma",
    "or": "oregon",
    "pa": "pennsylvania",
    "ri": "rhode island",
    "sc": "south carolina",
    "sd": "south dakota",
    "tn": "tennessee",
    "tx": "texas",
    "ut": "utah",
    "vt": "vermont",
    "va": "virginia",
    "wa": "washington",
    "wv": "west virginia",
    "wi": "wisconsin",
    "wy": "wyoming",
}


class DataForSEOLiveProvider:
    provider_name = "dataforseo-live"
    api_version = "v3"

    def __init__(self, settings: Settings | None = None, timeout_seconds: float = 45.0) -> None:
        self.settings = settings or get_settings()
        validate_runtime_mode(self.settings, DataMode.live)
        self.timeout_seconds = timeout_seconds

    async def check_account(self) -> dict[str, Any]:
        payload = await self._get("/v3/appendix/user_data")
        task = self._first_task(payload)
        return {
            "status_code": task.get("status_code"),
            "status_message": task.get("status_message"),
            "result": task.get("result", []),
        }

    async def resolve_location(self, query: str) -> ResolvedLocation:
        cleaned = query.strip()
        if not cleaned:
            raise DataForSEOError("A market/location is required for live DataForSEO scans.")

        payload = await self._get(f"/v3/serp/google/locations/{self.settings_country_code.lower()}")
        locations = self._first_result(payload)
        if not locations:
            raise DataForSEOError("DataForSEO returned no Google locations for the configured country.")

        match = self._best_location_match(cleaned, cast(list[dict[str, Any]], locations))
        if match is None:
            raise DataForSEOError(
                f"Could not resolve '{query}' to a DataForSEO Google location. Try 'City, ST' or a larger nearby city."
            )

        location_name = str(match.get("location_name") or cleaned)
        location_code = str(match.get("location_code") or "")
        region = str(match.get("location_name_parent") or "")
        city = location_name.split(",")[0].strip()
        parsed_state = self._extract_state(cleaned, location_name)
        is_zip = cleaned.isdigit()
        market = Market(
            id=slugify(cleaned),
            display_name=cleaned if not is_zip else f"ZIP {cleaned}",
            type=LocationType.postal_code if is_zip else LocationType.city,
            country_code=self.settings_country_code,
            state=parsed_state,
            cities=[] if is_zip else [city],
            postal_codes=[cleaned] if is_zip else [],
            provider_location_code=location_code,
            provider_location_name=location_name,
            resolution_metadata={
                "original_input": cleaned,
                "provider": self.provider_name,
                "matched_location": location_name,
                "matched_region": region,
                "keyword_volume_granularity": "nearest_city" if is_zip else "city",
            },
        )
        notes = ["ZIP resolved to the nearest DataForSEO supported Google location."] if is_zip else []
        return ResolvedLocation(
            original_input=cleaned,
            market=market,
            provider_location_code=location_code,
            provider_location_name=location_name,
            granularity=market.type.value,
            notes=notes,
        )

    async def discover_keywords(
        self, service: ServiceFamily, market: Market
    ) -> list[KeywordCandidate]:
        candidates: list[KeywordCandidate] = []
        seeds = (service.seed_queries or [service.display_name])[:3]
        for seed in seeds:
            task = {
                "keyword": seed,
                "language_code": "en",
                "limit": 20,
                "include_seed_keyword": True,
                **self._location_payload(market),
            }
            payload = await self._post("/v3/dataforseo_labs/google/keyword_suggestions/live", [task])
            items = self._extract_items(payload)
            for item in items:
                keyword = str(item.get("keyword") or item.get("se_results_keyword") or "").strip()
                if keyword:
                    candidates.append(
                        KeywordCandidate(keyword=keyword, source="dataforseo:keyword_suggestions")
                    )

        if not candidates:
            candidates = [KeywordCandidate(keyword=seed, source="seed") for seed in seeds]
        return candidates

    async def get_keyword_metrics(self, keywords: list[str], market: Market) -> list[KeywordMetric]:
        if not keywords:
            return []
        task = {
            "keywords": keywords[:50],
            "language_code": "en",
            **self._location_payload(market),
        }
        payload = await self._post(
            "/v3/dataforseo_labs/google/historical_search_volume/live",
            [task],
        )
        metrics: list[KeywordMetric] = []
        for item in self._extract_items(payload):
            keyword = str(item.get("keyword") or "").strip()
            if not keyword:
                continue
            keyword_info = self._as_dict(item.get("keyword_info"))
            monthly = item.get("monthly_searches") or keyword_info.get("monthly_searches") or []
            metrics.append(
                KeywordMetric(
                    keyword=keyword,
                    canonical_keyword=slugify(keyword).replace("-", " "),
                    intent=str(item.get("search_intent_info", {}).get("main_intent") or self._infer_intent(keyword)),
                    search_volume=self._to_int(keyword_info.get("search_volume") or item.get("search_volume")),
                    cpc=self._to_float(keyword_info.get("cpc") or item.get("cpc")),
                    paid_competition=self._to_float(
                        keyword_info.get("competition") or item.get("competition")
                    ),
                    monthly_history=self._monthly_history(monthly),
                    source="dataforseo:historical_search_volume",
                    market_granularity=market.type.value,
                )
            )
        return metrics

    async def get_serp_snapshot(self, keyword: str, market: Market) -> SerpSnapshot:
        task = {
            "keyword": keyword,
            "language_code": "en",
            "device": "desktop",
            "depth": 10,
            **self._location_payload(market),
        }
        payload = await self._post("/v3/serp/google/organic/live/advanced", [task])
        result = self._first_result(payload)
        result_obj = result[0] if result and isinstance(result[0], dict) else {}
        items = cast(list[dict[str, Any]], result_obj.get("items") or [])
        serp_results: list[SerpResult] = []
        features: set[str] = set()
        for index, item in enumerate(items, start=1):
            result_type = str(item.get("type") or "organic")
            features.add(result_type)
            url = str(item.get("url") or "")
            if not url:
                continue
            domain = str(item.get("domain") or urlparse(url).netloc)
            serp_results.append(
                SerpResult(
                    order=self._to_int(item.get("rank_absolute")) or index,
                    result_type=result_type,
                    url=url,
                    domain=domain,
                    title=str(item.get("title") or domain),
                    description=str(item.get("description") or ""),
                )
            )
        return SerpSnapshot(
            query=keyword,
            market_id=market.id,
            device="desktop",
            features_present=sorted(features - {"organic"}),
            results=serp_results,
        )

    async def get_competitor_metrics(self, urls: list[str]) -> list[CompetitorMetric]:
        metrics: list[CompetitorMetric] = []
        seen: set[str] = set()
        for url in urls[:5]:
            target = self._target_domain(url)
            if not target or target in seen:
                continue
            seen.add(target)
            payload = await self._post(
                "/v3/backlinks/summary/live",
                [{"target": target, "include_subdomains": True}],
            )
            result = self._first_result(payload)
            row = cast(dict[str, Any], result[0]) if result and isinstance(result[0], dict) else {}
            metrics.append(
                CompetitorMetric(
                    url=url,
                    domain=target,
                    referring_domains=self._to_int(row.get("referring_domains")),
                    backlinks=self._to_int(row.get("backlinks")),
                    authority=self._to_float(
                        row.get("rank") or row.get("domain_rank") or row.get("page_rank")
                    ),
                    page_type="unknown",
                )
            )
        return metrics

    async def find_providers(
        self, service: ServiceFamily, market: Market
    ) -> list[ProviderCandidate]:
        task: dict[str, Any] = {
            "language_code": "en",
            "limit": 10,
            **self._location_payload(market),
        }
        if service.provider_categories:
            task["categories"] = service.provider_categories[:10]
        else:
            task["description"] = service.display_name

        payload = await self._post("/v3/business_data/business_listings/search/live", [task])
        providers: list[ProviderCandidate] = []
        for item in self._extract_items(payload):
            name = str(item.get("title") or item.get("name") or "").strip()
            if not name:
                continue
            rating = self._as_dict(item.get("rating"))
            address = self._format_address(item.get("address_info") or item.get("address"))
            providers.append(
                ProviderCandidate(
                    name=name,
                    website=self._clean_optional_str(item.get("url") or item.get("domain")),
                    phone=self._clean_optional_str(item.get("phone")),
                    address=address,
                    service_area=market.display_name,
                    category=self._clean_optional_str(item.get("category")),
                    rating=self._to_float(rating.get("value") or item.get("rating")),
                    review_count=self._to_int(rating.get("votes_count") or item.get("review_count")),
                    business_status=str(item.get("status") or "unknown"),
                    contact_confidence=0.65 if item.get("url") or item.get("phone") else 0.35,
                    source="dataforseo:business_listings",
                )
            )
        return providers

    @property
    def settings_country_code(self) -> str:
        return "US"

    async def _get(self, path: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(path)
        return self._parse_response(response)

    async def _post(self, path: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(path, json=tasks)
        return self._parse_response(response)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.dataforseo.com",
            auth=(self.settings.dataforseo_login, self.settings.dataforseo_password),
            timeout=self.timeout_seconds,
        )

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = cast(dict[str, Any], response.json())
        except ValueError as exc:
            raise DataForSEOError(f"DataForSEO returned non-JSON response: HTTP {response.status_code}") from exc
        if response.status_code >= 400:
            message = payload.get("status_message") or response.text
            raise DataForSEOError(f"DataForSEO HTTP {response.status_code}: {message}")
        status_code = self._to_int(payload.get("status_code"))
        if status_code is not None and status_code >= 40000:
            raise DataForSEOError(str(payload.get("status_message") or f"DataForSEO error {status_code}"))
        for task in cast(list[dict[str, Any]], payload.get("tasks") or []):
            task_code = self._to_int(task.get("status_code"))
            if task_code is not None and task_code >= 40000:
                raise DataForSEOError(str(task.get("status_message") or f"DataForSEO task error {task_code}"))
        return payload

    def _first_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        tasks = payload.get("tasks") or []
        if not tasks or not isinstance(tasks[0], dict):
            raise DataForSEOError("DataForSEO response did not include a task.")
        return cast(dict[str, Any], tasks[0])

    def _first_result(self, payload: dict[str, Any]) -> list[Any]:
        task = self._first_task(payload)
        result = task.get("result") or []
        return result if isinstance(result, list) else []

    def _extract_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for result in self._first_result(payload):
            if isinstance(result, dict):
                raw_items = result.get("items") or []
                if isinstance(raw_items, list):
                    items.extend(cast(list[dict[str, Any]], raw_items))
        return items

    def _location_payload(self, market: Market) -> dict[str, Any]:
        if market.provider_location_code:
            return {"location_code": int(market.provider_location_code)}
        if market.provider_location_name:
            return {"location_name": market.provider_location_name}
        return {"location_name": market.display_name}

    def _best_location_match(
        self,
        query: str,
        locations: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        normalized_query = self._normalize_location(query)
        tokens = set(normalized_query.split())
        expanded_tokens = {
            STATE_NAMES.get(token, token)
            for token in tokens
            if token not in {"usa", "us", "united", "states"}
        }
        city_token = self._normalize_location(query.split(",")[0])

        best: tuple[int, dict[str, Any]] | None = None
        for location in locations:
            name = str(location.get("location_name") or "")
            normalized_name = self._normalize_location(name)
            score = 0
            if normalized_query == normalized_name:
                score += 100
            if normalized_name.startswith(city_token):
                score += 35
            score += 10 * len(expanded_tokens.intersection(normalized_name.split()))
            if all(token in normalized_name.split() for token in expanded_tokens):
                score += 25
            if score and (best is None or score > best[0]):
                best = (score, location)
        return best[1] if best else None

    def _normalize_location(self, value: str) -> str:
        expanded = re.sub(
            r"\b([a-z]{2})\b",
            lambda match: STATE_NAMES.get(match.group(1).lower(), match.group(1).lower()),
            value.lower(),
        )
        return re.sub(r"[^a-z0-9]+", " ", expanded).strip()

    def _extract_state(self, query: str, location_name: str) -> str | None:
        query_tokens = self._normalize_location(query).split()
        for token in query_tokens:
            if token in STATE_NAMES.values():
                return token.title()
        location_tokens = self._normalize_location(location_name).split()
        for state in STATE_NAMES.values():
            parts = state.split()
            if all(part in location_tokens for part in parts):
                return state.title()
        return None

    def _monthly_history(self, monthly: Any) -> list[int]:
        if not isinstance(monthly, list):
            return []
        values: list[int] = []
        for row in monthly:
            if isinstance(row, dict):
                value = self._to_int(row.get("search_volume"))
                if value is not None:
                    values.append(value)
        return values

    def _infer_intent(self, keyword: str) -> str:
        transactional_terms = ("repair", "replacement", "installation", "emergency", "near me", "service")
        return "transactional" if any(term in keyword.lower() for term in transactional_terms) else "commercial"

    def _target_domain(self, url: str) -> str:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return parsed.netloc.removeprefix("www.")

    def _format_address(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if not isinstance(value, dict):
            return None
        parts = [
            value.get("address"),
            value.get("city"),
            value.get("region"),
            value.get("zip"),
            value.get("country_code"),
        ]
        text = ", ".join(str(part) for part in parts if part)
        return text or None

    def _clean_optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _as_dict(self, value: Any) -> dict[str, Any]:
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _to_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None

    def _to_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None
