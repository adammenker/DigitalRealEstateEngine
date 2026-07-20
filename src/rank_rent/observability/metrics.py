from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

API_REQUESTS = Counter(
    "rank_rent_api_requests_total",
    "API requests.",
    ("method", "route", "status"),
)
API_ERRORS = Counter(
    "rank_rent_api_errors_total",
    "API responses with a 5xx status.",
    ("route",),
)
API_LATENCY = Histogram(
    "rank_rent_api_request_duration_seconds",
    "API request latency.",
    ("method", "route"),
)
AUTH_FAILURES = Counter("rank_rent_authentication_failures_total", "Authentication failures.")
RATE_LIMIT_RESPONSES = Counter("rank_rent_rate_limit_responses_total", "Rate-limited requests.")

QUEUE_DEPTH = Gauge("rank_rent_scan_queue_depth", "Queued scans.")
OLDEST_QUEUED_SECONDS = Gauge(
    "rank_rent_scan_oldest_queued_age_seconds",
    "Age of oldest queued scan.",
)
ACTIVE_JOBS = Gauge("rank_rent_scan_active_jobs", "Active scan jobs.")
STALE_JOBS = Gauge("rank_rent_scan_stale_jobs", "Stale running scans.")
FAILED_JOBS = Gauge("rank_rent_scan_failed_jobs", "Failed scan jobs.")
CANCELLED_JOBS = Gauge("rank_rent_scan_cancelled_jobs", "Cancelled scan jobs.")
WORKER_HEARTBEAT_AGE = Gauge(
    "rank_rent_worker_heartbeat_age_seconds",
    "Age of the newest worker heartbeat.",
)
WORKER_RETRIES = Counter("rank_rent_worker_retries_total", "Durable scan retries.")
WORKER_STAGE_DURATION = Histogram(
    "rank_rent_worker_stage_duration_seconds",
    "Worker stage duration.",
    ("stage",),
)

PROVIDER_CALLS = Counter(
    "rank_rent_provider_calls_total",
    "Provider calls.",
    ("provider", "endpoint", "status"),
)
PROVIDER_COST = Counter(
    "rank_rent_provider_cost_usd_total",
    "Provider cost in USD.",
    ("provider", "endpoint"),
)
PROVIDER_LATENCY = Histogram(
    "rank_rent_provider_request_duration_seconds",
    "Provider response latency.",
    ("provider", "endpoint"),
)
PROVIDER_CACHE_HITS = Counter(
    "rank_rent_provider_cache_hits_total",
    "Provider cache hits.",
    ("provider", "endpoint"),
)
PROVIDER_SCHEMA_MISMATCHES = Counter(
    "rank_rent_provider_schema_mismatches_total",
    "Provider schema mismatches.",
    ("provider", "endpoint"),
)
PROVIDER_RATE_LIMITS = Counter(
    "rank_rent_provider_rate_limits_total",
    "Provider rate-limit responses.",
    ("provider", "endpoint"),
)

DISCOVERY_SCANS = Counter(
    "rank_rent_discovery_scans_total",
    "Discovery scans.",
    ("profile", "status"),
)
EVIDENCE_GATE_RESULTS = Counter(
    "rank_rent_evidence_gate_results_total",
    "Evidence gate results.",
    ("status",),
)
RANKABLE_OPPORTUNITIES = Gauge(
    "rank_rent_rankable_opportunities",
    "Current rankable opportunity count.",
)
RESCORES = Counter("rank_rent_rescores_total", "Opportunity rescores.")
DISCOVERY_CONFIDENCE = Counter(
    "rank_rent_discovery_confidence_total",
    "Discovery confidence distribution.",
    ("confidence",),
)
SCORE_VERSIONS = Counter(
    "rank_rent_score_versions_total",
    "Produced score versions.",
    ("version",),
)
COST_RECONCILIATION_FAILURES = Counter(
    "rank_rent_cost_reconciliation_failures_total",
    "Completed scans with an incomplete cost ledger.",
)
SCAN_COST_LIMIT_BLOCKS = Counter(
    "rank_rent_scan_cost_limit_blocks_total",
    "Scan plans blocked because their estimated uncached cost exceeded the limit.",
)
UNPLANNED_PAID_CALLS = Counter(
    "rank_rent_unplanned_paid_calls_total",
    "Unplanned paid-provider call attempts blocked or detected.",
)
UNPLANNED_PAID_CALLS.inc(0)

DATABASE_AVAILABLE = Gauge(
    "rank_rent_database_available",
    "Whether the application database is reachable.",
)

LEAD_SUBMISSIONS = Counter(
    "rank_rent_lead_submissions_total",
    "Lead submissions.",
    ("channel", "status"),
)
ROUTING_FAILURES = Counter(
    "rank_rent_routing_failures_total",
    "Lead routing failures.",
    ("channel",),
)
DEPLOYED_PROPERTIES = Gauge("rank_rent_deployed_properties", "Deployed properties.")
TRACKED_CALLS = Counter(
    "rank_rent_tracked_calls_total",
    "Tracked property calls.",
    ("status",),
)
PROVIDER_RESPONSE_OUTCOMES = Counter(
    "rank_rent_provider_response_outcomes_total",
    "Provider response outcomes.",
    ("outcome",),
)


def record_api_request(method: str, route: str, status_code: int, duration: float) -> None:
    status = str(status_code)
    API_REQUESTS.labels(method=method, route=route, status=status).inc()
    API_LATENCY.labels(method=method, route=route).observe(duration)
    if status_code >= 500:
        API_ERRORS.labels(route=route).inc()
