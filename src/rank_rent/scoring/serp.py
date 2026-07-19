from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from rank_rent.domain.models import Market, ProviderCandidate, SerpResult, ServiceFamily, slugify

SERP_CLASSIFIER_CONFIG = Path("config/serp_classification.yaml")


@lru_cache(maxsize=1)
def _classifier_config() -> dict[str, Any]:
    path = SERP_CLASSIFIER_CONFIG
    if not path.exists():
        path = Path(__file__).resolve().parents[3] / SERP_CLASSIFIER_CONFIG
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


def classify_result(
    result: SerpResult,
    *,
    service: ServiceFamily | None = None,
    market: Market | None = None,
    providers: list[ProviderCandidate] | None = None,
) -> SerpResult:
    if result.manual_override:
        classification = result.manual_override
        return _with_classification(
            result,
            classification=classification,
            confidence=1.0,
            matched_rules=["manual_override"],
            evidence={"override_reason": result.override_reason},
        )

    config = _classifier_config()
    normalized_domain = _normalize_domain(result.domain or urlparse(result.url).netloc)
    path = urlparse(result.url).path.lower()
    text = " ".join([result.title, result.description, path, normalized_domain]).lower()

    for rule in config.get("rules", []):
        if str(rule.get("classification")) == "local_provider":
            local_match = _local_provider_match(
                rule,
                result,
                normalized_domain,
                text,
                service=service,
                market=market,
                providers=providers or [],
            )
            if local_match is None:
                continue
            return _with_classification(
                result,
                classification="local_provider",
                confidence=float(rule.get("confidence") or 0.5),
                matched_rules=[str(rule["id"])],
                evidence=local_match,
            )
        if _rule_matches(rule, normalized_domain, text):
            return _with_classification(
                result,
                classification=str(rule["classification"]),
                confidence=float(rule.get("confidence") or 0.5),
                matched_rules=[str(rule["id"])],
                evidence={
                    "domain": normalized_domain,
                    "title": result.title,
                    "rule": rule["id"],
                },
            )

    return _with_classification(
        result,
        classification="unknown",
        confidence=0.35,
        matched_rules=[],
        evidence={"domain": normalized_domain, "title": result.title},
    )


def _rule_matches(rule: dict[str, Any], domain: str, text: str) -> bool:
    domains = [str(item).lower() for item in rule.get("domains", [])]
    if any(domain == item or domain.endswith(f".{item}") for item in domains):
        return True

    suffixes = [str(item).lower() for item in rule.get("domain_suffixes", [])]
    if any(domain.endswith(item) for item in suffixes):
        return True

    terms = [str(item).lower() for item in rule.get("text_contains", [])]
    return any(term in text for term in terms)


def _local_provider_match(
    rule: dict[str, Any],
    result: SerpResult,
    domain: str,
    text: str,
    *,
    service: ServiceFamily | None,
    market: Market | None,
    providers: list[ProviderCandidate],
) -> dict[str, Any] | None:
    if service is None or market is None:
        return None
    service_tokens = _important_tokens(service.display_name)
    service_tokens.update(token for query in service.seed_queries for token in _important_tokens(query))
    market_tokens = _market_tokens(market)
    result_tokens = _important_tokens(text)
    configured_service_terms = {
        str(item).lower() for item in rule.get("service_terms", []) if str(item).strip()
    }
    service_overlap = sorted(result_tokens & service_tokens)
    service_term_overlap = sorted(result_tokens & configured_service_terms)
    market_overlap = sorted(result_tokens & market_tokens)
    provider_match = _provider_listing_match(domain, result, providers)
    identity_terms = [str(item).lower() for item in rule.get("business_identity_terms", [])]
    identity_matches = [term for term in identity_terms if term in text]
    clear_business_identity = bool(identity_matches) or _business_like_domain(domain)

    has_service_relevance = bool(service_overlap or service_term_overlap)
    has_market_relevance = bool(market_overlap)
    has_business_evidence = provider_match is not None or clear_business_identity
    if not (has_service_relevance and has_market_relevance and has_business_evidence):
        return None
    return {
        "domain": domain,
        "title": result.title,
        "service_overlap": service_overlap,
        "service_term_overlap": service_term_overlap,
        "market_overlap": market_overlap,
        "provider_match": provider_match,
        "business_identity_terms": identity_matches,
        "business_like_domain": _business_like_domain(domain),
        "rule": rule["id"],
    }


def _provider_listing_match(
    domain: str,
    result: SerpResult,
    providers: list[ProviderCandidate],
) -> dict[str, Any] | None:
    result_tokens = _important_tokens(f"{result.title} {result.description} {domain}")
    for provider in providers:
        provider_domain = _provider_domain(provider)
        if provider_domain and (domain == provider_domain or domain.endswith(f".{provider_domain}")):
            return {"type": "website_domain", "provider_name": provider.name}
        name_tokens = _important_tokens(provider.name)
        if name_tokens and len(result_tokens & name_tokens) / max(1, len(name_tokens)) >= 0.6:
            return {
                "type": "business_name",
                "provider_name": provider.name,
                "matched_tokens": sorted(result_tokens & name_tokens),
            }
    return None


def _provider_domain(provider: ProviderCandidate) -> str | None:
    if not provider.website:
        return None
    return _normalize_domain(urlparse(provider.website).netloc or provider.website)


def _market_tokens(market: Market) -> set[str]:
    values = [market.display_name, market.state or "", market.country_code]
    values.extend(market.cities)
    values.extend(market.postal_codes)
    return {token for value in values for token in _important_tokens(value)}


def _important_tokens(value: str) -> set[str]:
    stop = {
        "a",
        "and",
        "at",
        "best",
        "for",
        "in",
        "near",
        "of",
        "the",
        "to",
        "us",
        "usa",
        "www",
    }
    return {token for token in slugify(value).replace("-", " ").split() if len(token) > 1 and token not in stop}


def _business_like_domain(domain: str) -> bool:
    tokens = set(domain.replace(".", " ").replace("-", " ").split())
    business_terms = {
        "co",
        "company",
        "contractors",
        "pros",
        "service",
        "services",
        "plumbing",
        "roofing",
        "hvac",
        "repair",
    }
    return bool(tokens & business_terms)


def _with_classification(
    result: SerpResult,
    *,
    classification: str,
    confidence: float,
    matched_rules: list[str],
    evidence: dict[str, Any],
) -> SerpResult:
    return result.model_copy(
        update={
            "classification": classification,
            "is_local_provider": classification == "local_provider",
            "is_directory": classification == "directory",
            "is_national_brand": classification == "national_brand",
            "is_lead_generation_site": classification == "lead_generator",
            "classification_confidence": round(confidence, 2),
            "classifier_version": str(_classifier_config().get("version", "v2")),
            "matched_rules": matched_rules,
            "classification_evidence": evidence,
        }
    )


def _normalize_domain(domain: str) -> str:
    return domain.lower().removeprefix("www.").strip()
