from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch


class CalibrationNetworkAccessError(RuntimeError):
    pass


class NetworkGuard:
    def __init__(self) -> None:
        self.attempt_count = 0

    def deny(self, *_args: Any, **_kwargs: Any) -> None:
        self.attempt_count += 1
        raise CalibrationNetworkAccessError(
            "Calibration is offline-only; a network connection was attempted."
        )


@contextmanager
def block_network() -> Iterator[NetworkGuard]:
    guard = NetworkGuard()
    with (
        patch.object(socket.socket, "connect", guard.deny),
        patch.object(socket.socket, "connect_ex", guard.deny),
        patch.object(socket, "create_connection", guard.deny),
    ):
        yield guard
