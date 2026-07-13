from __future__ import annotations

import re

from rank_rent.domain.interfaces import DomainAvailabilityProvider
from rank_rent.domain.models import DomainCandidate, Market, ServiceFamily, slugify

RISK_TERMS = {"best", "official", "guaranteed", "number1"}
BRAND_WORDS = ["clearpath", "local", "summit", "harbor"]


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", slugify(value))


def _score_domain(domain: str, service_term: str, market_term: str) -> tuple[float, float, float, float, list[str]]:
    stem = domain.removesuffix(".com")
    risks = [term for term in RISK_TERMS if term in stem]
    readability = max(1, 10 - max(0, len(stem) - 20) * 0.4)
    relevance = 5 + (2 if service_term in stem else 0) + (2 if market_term and market_term in stem else 0)
    brandability = 8 if any(word in stem for word in BRAND_WORDS) else 6
    expansion = 8 if "guide" not in stem else 6
    if re.search(r"([a-z])\1\1", stem):
        risks.append("awkward_repeated_letters")
        readability -= 2
    return readability, relevance, brandability, expansion, risks


async def generate_domain_candidates(
    service: ServiceFamily, market: Market, checker: DomainAvailabilityProvider
) -> list[DomainCandidate]:
    service_term = _compact(service.display_name.replace("services", ""))
    city = market.cities[0] if market.cities else market.display_name
    market_term = _compact(city)
    region_term = _compact(market.display_name.replace(",", ""))
    state = (market.state or "").lower()
    patterns = [
        ("{city}{service}help.com", f"{market_term}{service_term}help.com"),
        ("{region}{service}.com", f"{region_term}{service_term}.com"),
        ("{service}pros{state}.com", f"{service_term}pros{state}.com"),
        ("{city}{service}guide.com", f"{market_term}{service_term}guide.com"),
        ("{brand_word}{service}.com", f"clearpath{service_term}.com"),
        ("{region}homehelp.com", f"{region_term}homehelp.com"),
    ]
    candidates: list[DomainCandidate] = []
    for pattern, domain in dict(patterns).items():
        availability = await checker.check(domain)
        readability, relevance, brandability, expansion, risks = _score_domain(
            domain, service_term, market_term
        )
        candidates.append(
            DomainCandidate(
                domain=domain,
                pattern_used=pattern,
                availability_status=availability.status,
                readability_score=round(readability, 2),
                relevance_score=round(relevance, 2),
                brandability_score=round(brandability, 2),
                expansion_score=round(expansion, 2),
                risk_flags=risks,
                checked_at=availability.checked_at,
            )
        )
    candidates.sort(
        key=lambda c: (
            c.availability_status != "available",
            -(c.readability_score + c.relevance_score + c.brandability_score + c.expansion_score),
        )
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate.rank = index
    return candidates

