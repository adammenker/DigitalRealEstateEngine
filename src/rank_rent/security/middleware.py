from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware

from rank_rent.observability.context import request_id_var, trace_id_var, user_id_var
from rank_rent.observability.logging import log_event
from rank_rent.observability.metrics import (
    AUTH_FAILURES,
    RATE_LIMIT_RESPONSES,
    record_api_request,
)
from rank_rent.security.auth import (
    AuthenticationError,
    OIDCVerifier,
    Permission,
    authenticate_request,
)
from rank_rent.settings import Settings

PUBLIC_HEALTH_PATHS = frozenset(
    {"/healthz", "/readyz", "/live", "/ready", "/health/dependencies"}
)


@dataclass
class _Window:
    timestamps: deque[float]


class FixedWindowRateLimiter:
    def __init__(self, requests: int, window_seconds: int) -> None:
        self.requests = requests
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = defaultdict(lambda: _Window(deque()))

    async def allow(self, key: str, now: float) -> tuple[bool, int]:
        timestamps = self._windows[key].timestamps
        threshold = now - self.window_seconds
        while timestamps and timestamps[0] <= threshold:
            timestamps.popleft()
        if len(timestamps) >= self.requests:
            retry_after = max(1, int(self.window_seconds - (now - timestamps[0])))
            return False, retry_after
        timestamps.append(now)
        return True, 0


class RedisFixedWindowRateLimiter:
    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
      redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    local ttl = redis.call('TTL', KEYS[1])
    return {count, ttl}
    """

    def __init__(self, url: str, requests: int, window_seconds: int) -> None:
        self.client = Redis.from_url(url, decode_responses=True)
        self.requests = requests
        self.window_seconds = window_seconds

    async def allow(self, key: str, now: float) -> tuple[bool, int]:
        del now
        digest = hashlib.sha256(key.encode()).hexdigest()
        result: Any = await self.client.eval(
            self._SCRIPT,
            1,
            f"rank-rent:rate-limit:{digest}",
            self.window_seconds,
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise RedisError("Unexpected rate-limit response.")
        count, ttl = int(result[0]), int(result[1])
        return count <= self.requests, max(1, ttl)


MUTATION_PERMISSIONS: tuple[tuple[str, str, Permission, str], ...] = (
    ("POST", "/api/market-prefilter", Permission.run_testing_scan, "market_prefilter.run"),
    ("POST", "/api/scans", Permission.run_testing_scan, "scan.create"),
    ("POST", "/api/opportunities/", Permission.override_evidence, "opportunity.change"),
    ("POST", "/api/scans/", Permission.run_testing_scan, "scan.change"),
    ("POST", "/api/discovery-templates", Permission.change_production_limits, "template.create"),
    ("PUT", "/api/discovery-templates/", Permission.change_production_limits, "template.update"),
    ("DELETE", "/api/discovery-templates/", Permission.change_production_limits, "template.archive"),
    ("POST", "/api/batch-scan-plans", Permission.run_testing_scan, "batch.create"),
    ("POST", "/api/batch-scan-plans/", Permission.run_testing_scan, "batch.change"),
    ("POST", "/scan", Permission.run_testing_scan, "scan.create"),
)


def _mutation_policy(method: str, path: str) -> tuple[Permission, str] | None:
    for expected_method, prefix, permission, event_type in MUTATION_PERMISSIONS:
        if method == expected_method and (
            path == prefix or (prefix.endswith("/") and path.startswith(prefix))
        ):
            if path.endswith("/promote"):
                return Permission.run_full_scan, "scan.promote"
            if path.endswith("/rescore"):
                return Permission.override_evidence, "opportunity.rescore"
            if path.endswith("/review/transition"):
                return Permission.approve_opportunity, "opportunity.review.transition"
            if path.endswith("/review/owner"):
                return Permission.approve_opportunity, "opportunity.review.owner"
            if "/overrides" in path:
                return Permission.override_evidence, "opportunity.evidence_override"
            if path.endswith("/cancel"):
                return Permission.run_testing_scan, "scan.cancel"
            if path.endswith("/retry"):
                return Permission.run_testing_scan, "scan.retry"
            return permission, event_type
    return None


class SecurityObservabilityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings
        self.oidc = OIDCVerifier(settings) if settings.auth_mode == "oidc" else None
        self.rate_limiter = (
            RedisFixedWindowRateLimiter(
                settings.redis_url,
                settings.rate_limit_requests,
                settings.rate_limit_window_seconds,
            )
            if settings.rate_limit_backend == "redis"
            else FixedWindowRateLimiter(
                settings.rate_limit_requests,
                settings.rate_limit_window_seconds,
            )
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        path = request.url.path
        requested_id = request.headers.get("x-request-id", "")
        request_id = (
            requested_id
            if re.fullmatch(r"[A-Za-z0-9._-]{8,64}", requested_id)
            else request_id_var.new()
        )
        trace_id = trace_id_var.from_traceparent(request.headers.get("traceparent"))
        request_token = request_id_var.set(request_id)
        trace_token = trace_id_var.set(trace_id)
        request.state.request_id = request_id
        user_token = None
        status_code = 500
        try:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self.settings.max_request_body_bytes:
                status_code = 413
                return self._error(413, "Request body is too large.", request_id)

            principal = None
            if path not in PUBLIC_HEALTH_PATHS:
                try:
                    principal = authenticate_request(request, self.settings, self.oidc)
                    request.state.principal = principal
                    user_token = user_id_var.set(principal.user_id)
                except AuthenticationError:
                    AUTH_FAILURES.inc()
                    status_code = 401
                    return self._error(
                        401,
                        "Authentication is required.",
                        request_id,
                        {"WWW-Authenticate": "Bearer"},
                    )
                policy = _mutation_policy(request.method, path)
                if policy and not principal.permits(policy[0]):
                    status_code = 403
                    return self._error(
                        403,
                        "You do not have permission to perform this action.",
                        request_id,
                    )

                key = principal.user_id
                try:
                    allowed, retry_after = await self.rate_limiter.allow(
                        key,
                        time.monotonic(),
                    )
                except RedisError:
                    status_code = 503
                    log_event("security.rate_limit_backend_unavailable")
                    return self._error(
                        503,
                        "Request security service is unavailable.",
                        request_id,
                    )
                if not allowed:
                    RATE_LIMIT_RESPONSES.inc()
                    status_code = 429
                    return self._error(
                        429,
                        "Request rate limit exceeded.",
                        request_id,
                        {"Retry-After": str(retry_after)},
                    )

            response = await call_next(request)
            status_code = response.status_code
            self._security_headers(response)
            response.headers["X-Request-ID"] = request_id
            response.headers["traceparent"] = f"00-{trace_id}-{secrets.token_hex(8)}-01"
            return response
        except ValueError:
            status_code = 400
            return self._error(400, "Invalid request metadata.", request_id)
        finally:
            duration = time.perf_counter() - started
            record_api_request(request.method, _route_template(request), status_code, duration)
            log_event(
                "http.request.completed",
                method=request.method,
                path=path,
                status_code=status_code,
                duration_ms=round(duration * 1000, 3),
            )
            request_id_var.reset(request_token)
            trace_id_var.reset(trace_token)
            if user_token is not None:
                user_id_var.reset(user_token)

    def _error(
        self,
        status_code: int,
        detail: str,
        request_id: str,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail, "request_id": request_id},
            headers=headers,
        )
        self._security_headers(response)
        response.headers["X-Request-ID"] = request_id
        return response

    def _security_headers(self, response: Response) -> None:
        response.headers["Content-Security-Policy"] = self.settings.content_security_policy
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if self.settings.app_env in {"staging", "production"}:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Cache-Control"] = "no-store"


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)
