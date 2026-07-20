from __future__ import annotations

import secrets
from contextvars import ContextVar, Token


class CorrelationContext:
    def __init__(self, name: str, *, byte_length: int) -> None:
        self._value: ContextVar[str | None] = ContextVar(name, default=None)
        self.byte_length = byte_length

    def new(self) -> str:
        return secrets.token_hex(self.byte_length)

    def get(self) -> str | None:
        return self._value.get()

    def set(self, value: str) -> Token[str | None]:
        return self._value.set(value)

    def reset(self, token: Token[str | None]) -> None:
        self._value.reset(token)

    def from_traceparent(self, traceparent: str | None) -> str:
        if traceparent:
            parts = traceparent.split("-")
            if len(parts) == 4 and len(parts[1]) == 32:
                try:
                    int(parts[1], 16)
                    if parts[1] != "0" * 32:
                        return parts[1]
                except ValueError:
                    pass
        return self.new()


request_id_var = CorrelationContext("request_id", byte_length=16)
trace_id_var = CorrelationContext("trace_id", byte_length=16)
scan_run_id_var: ContextVar[int | None] = ContextVar("scan_run_id", default=None)
opportunity_id_var: ContextVar[int | None] = ContextVar("opportunity_id", default=None)
planned_request_id_var: ContextVar[str | None] = ContextVar("planned_request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

