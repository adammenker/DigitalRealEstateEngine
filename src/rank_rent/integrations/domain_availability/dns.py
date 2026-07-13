from __future__ import annotations

import asyncio
import socket

from rank_rent.domain.models import AvailabilityStatus, DomainAvailabilityResult


class DNSDomainAvailabilityProvider:
    async def check(self, domain: str) -> DomainAvailabilityResult:
        try:
            await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, domain, None), timeout=3)
        except socket.gaierror as exc:
            status = AvailabilityStatus.available if exc.errno == socket.EAI_NONAME else AvailabilityStatus.unknown
            return DomainAvailabilityResult(
                domain=domain,
                status=status,
                provider_raw_status=f"dns:{exc.errno}",
            )
        except TimeoutError:
            return DomainAvailabilityResult(
                domain=domain,
                status=AvailabilityStatus.unknown,
                provider_raw_status="dns:timeout",
            )
        return DomainAvailabilityResult(
            domain=domain,
            status=AvailabilityStatus.unavailable,
            provider_raw_status="dns:resolves",
        )
