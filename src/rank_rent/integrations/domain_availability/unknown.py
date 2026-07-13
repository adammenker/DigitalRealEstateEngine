from rank_rent.domain.models import AvailabilityStatus, DomainAvailabilityResult


class UnknownDomainAvailabilityProvider:
    async def check(self, domain: str) -> DomainAvailabilityResult:
        return DomainAvailabilityResult(
            domain=domain,
            status=AvailabilityStatus.unknown,
            provider_raw_status="not_configured",
        )
