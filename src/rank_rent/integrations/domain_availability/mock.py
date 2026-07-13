from rank_rent.domain.models import AvailabilityStatus, DomainAvailabilityResult


class MockDomainAvailabilityProvider:
    async def check(self, domain: str) -> DomainAvailabilityResult:
        status = AvailabilityStatus.unavailable if domain.startswith("best") else AvailabilityStatus.unknown
        if len(domain) < 24 and "official" not in domain:
            status = AvailabilityStatus.available
        return DomainAvailabilityResult(
            domain=domain,
            status=status,
            provider_raw_status=f"mock:{status.value}",
        )

