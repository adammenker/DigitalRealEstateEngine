from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from rank_rent.domain.models import SerpResult

SERP_CLASSIFIER_CONFIG = Path("config/serp_classification.yaml")


@lru_cache(maxsize=1)
def _classifier_config() -> dict[str, Any]:
    path = SERP_CLASSIFIER_CONFIG
    if not path.exists():
        path = Path(__file__).resolve().parents[3] / SERP_CLASSIFIER_CONFIG
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


def classify_result(result: SerpResult) -> SerpResult:
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
    text = " ".join([result.title, result.description, path]).lower()

    for rule in config.get("rules", []):
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
