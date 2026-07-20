from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from rank_rent.observability.context import (
    opportunity_id_var,
    planned_request_id_var,
    request_id_var,
    scan_run_id_var,
    trace_id_var,
    user_id_var,
)

_SECRET_KEYS = re.compile(
    r"(authorization|cookie|password|secret|token|api[-_]?key|credential)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|([a-z0-9._%+-]+)@([a-z0-9.-]+\.[a-z]{2,})"
)


def redact(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(
            lambda match: (
                f"{match.group(1)}[REDACTED]"
                if match.group(1)
                else f"{match.group(2)[:2]}***@{match.group(3)}"
            ),
            value,
        )
    return value


class JSONFormatter(logging.Formatter):
    def __init__(self, *, environment: str, service: str, version: str) -> None:
        super().__init__()
        self.environment = environment
        self.service = service
        self.version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "environment": self.environment,
            "service": self.service,
            "version": self.version,
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
            "scan_run_id": scan_run_id_var.get(),
            "opportunity_id": opportunity_id_var.get(),
            "planned_request_id": planned_request_id_var.get(),
            "user_id": user_id_var.get(),
            "event": getattr(record, "event", record.getMessage()),
        }
        fields = getattr(record, "structured_fields", {})
        if isinstance(fields, dict):
            payload.update(redact(fields))
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Error"
            payload["error"] = "Operation failed; inspect correlated diagnostics."
        return json.dumps(redact(payload), separators=(",", ":"), default=str)


def configure_logging(*, environment: str, service: str, version: str, level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter(environment=environment, service=service, version=version))
    root.handlers.clear()
    root.addHandler(handler)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    logging.getLogger("rank_rent.events").log(
        level,
        event,
        extra={"event": event, "structured_fields": fields},
    )

