from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from rank_rent.domain.models import KeywordCandidate, KeywordMetric, Market, ServiceFamily, slugify

TRANSACTIONAL_INTENTS = {"transactional", "commercial"}
LOCAL_MODIFIER_TOKENS = {"near", "me", "nearby", "local", "in"}
DEFAULT_AD_HOC_INTENT_MODIFIERS = ["repair", "replacement", "installation", "emergency"]
DEFAULT_NEGATIVE_PRODUCT_TERMS = ["parts", "kit", "manual", "lowes", "home depot"]


@dataclass(frozen=True)
class KeywordCluster:
    representative_keyword: str
    keywords: list[str]
    dedupe_method: str
    combined_volume: int | None


@dataclass(frozen=True)
class KeywordDecision:
    keyword: str
    canonical_keyword: str
    decision: str
    reason: str | None = None
    rank: int | None = None
    representative: bool = False
    cluster_id: str | None = None
    intent: str | None = None
    search_volume: int | None = None
    cpc: float | None = None
    granularity: str | None = None
    ranking_score: float | None = None


@dataclass(frozen=True)
class KeywordCandidatePlan:
    candidates: list[KeywordCandidate]
    included_keywords: list[str]
    decisions: list[KeywordDecision]


@dataclass(frozen=True)
class KeywordMetricPlan:
    metrics: list[KeywordMetric]
    scoring_metrics: list[KeywordMetric]
    selected_serp_keywords: list[str]
    clusters: list[KeywordCluster]
    decisions: list[KeywordDecision]


def service_keyword_terms(service: ServiceFamily) -> tuple[list[str], list[str]]:
    intent_modifiers = service.intent_modifiers or DEFAULT_AD_HOC_INTENT_MODIFIERS
    negative_terms = [
        *service.negative_terms,
        *(service.negative_product_terms or DEFAULT_NEGATIVE_PRODUCT_TERMS),
    ]
    return intent_modifiers, negative_terms


def service_seed_keywords(service: ServiceFamily) -> list[str]:
    base = service.display_name.lower()
    intent_modifiers, _ = service_keyword_terms(service)
    seeds = list(service.seed_queries or [base])
    seeds.extend(f"{base} {modifier}" for modifier in intent_modifiers)
    return _dedupe_strings(seeds)


def dedupe_and_filter_keywords(
    candidates: list[KeywordCandidate],
    negative_terms: list[str],
) -> list[KeywordCandidate]:
    return plan_keyword_candidates(candidates, negative_terms).candidates


def plan_keyword_candidates(
    candidates: list[KeywordCandidate],
    negative_terms: list[str],
) -> KeywordCandidatePlan:
    seen: set[str] = set()
    output: list[KeywordCandidate] = []
    decisions: list[KeywordDecision] = []
    negatives = [_normalize_keyword(term) for term in negative_terms if term.strip()]

    for candidate in candidates:
        canonical = _normalize_keyword(candidate.keyword)
        if not canonical:
            output.append(
                candidate.model_copy(
                    update={"included": False, "excluded_reason": "empty_keyword"}
                )
            )
            decisions.append(
                KeywordDecision(candidate.keyword, canonical, "excluded_negative", "empty_keyword")
            )
            continue
        negative = _matching_negative(canonical, negatives)
        if negative:
            output.append(
                candidate.model_copy(
                    update={"included": False, "excluded_reason": f"negative_term:{negative}"}
                )
            )
            decisions.append(
                KeywordDecision(
                    candidate.keyword,
                    canonical,
                    "excluded_negative",
                    f"negative_term:{negative}",
                )
            )
            continue
        if canonical in seen:
            output.append(
                candidate.model_copy(
                    update={"included": False, "excluded_reason": "duplicate_exact"}
                )
            )
            decisions.append(
                KeywordDecision(candidate.keyword, canonical, "excluded_duplicate", "duplicate_exact")
            )
            continue
        seen.add(canonical)
        output.append(candidate.model_copy(update={"included": True, "excluded_reason": None}))
        decisions.append(KeywordDecision(candidate.keyword, canonical, "candidate", "included_candidate"))

    included_keywords = [candidate.keyword for candidate in output if candidate.included]
    return KeywordCandidatePlan(output, included_keywords, decisions)


def rank_and_cluster_keyword_metrics(
    metrics: list[KeywordMetric],
    *,
    service: ServiceFamily,
    market: Market,
    selected_limit: int,
    existing_decisions: list[KeywordDecision] | None = None,
) -> KeywordMetricPlan:
    grouped: dict[str, list[KeywordMetric]] = {}
    for metric in metrics:
        key = _cluster_key(metric.canonical_keyword or metric.keyword, market)
        matched_key = _matching_cluster_key(key, grouped)
        grouped.setdefault(matched_key or key, []).append(metric)

    clusters: list[KeywordCluster] = []
    scoring_metrics: list[KeywordMetric] = []
    grouped_metrics: list[KeywordMetric] = []
    decisions = list(existing_decisions or [])

    for cluster_metrics in grouped.values():
        ranked_cluster = sorted(
            cluster_metrics,
            key=lambda metric: _metric_value_score(metric, service, market),
            reverse=True,
        )
        representative = ranked_cluster[0]
        cluster_volume = max((metric.search_volume or 0 for metric in ranked_cluster), default=0)
        clusters.append(
            KeywordCluster(
                representative_keyword=representative.keyword,
                keywords=[metric.keyword for metric in ranked_cluster],
                dedupe_method="close_variant" if len(ranked_cluster) > 1 else "exact",
                combined_volume=cluster_volume,
            )
        )
        scoring_metrics.append(
            representative.model_copy(
                update={"included": True, "search_volume": cluster_volume}
            )
        )
        for duplicate in ranked_cluster[1:]:
            grouped_metrics.append(
                duplicate.model_copy(
                    update={
                        "included": False,
                        "excluded_reason": f"grouped_with:{representative.keyword}",
                    }
                )
            )
            duplicate_score = _metric_value_score(duplicate, service, market)
            decisions.append(
                KeywordDecision(
                    keyword=duplicate.keyword,
                    canonical_keyword=_normalize_keyword(duplicate.canonical_keyword),
                    decision="grouped_variant",
                    reason=f"grouped_with:{representative.keyword}",
                    cluster_id=_cluster_id(representative.keyword),
                    intent=duplicate.intent,
                    search_volume=duplicate.search_volume,
                    cpc=duplicate.cpc,
                    granularity=duplicate.market_granularity,
                    ranking_score=round(duplicate_score, 2),
                )
            )

    ranked_scoring = sorted(
        scoring_metrics,
        key=lambda metric: _metric_value_score(metric, service, market),
        reverse=True,
    )
    selected = ranked_scoring[: max(0, selected_limit)]
    selected_keywords = {metric.keyword for metric in selected}
    ranked_metrics: list[KeywordMetric] = []
    for rank, metric in enumerate(ranked_scoring, start=1):
        is_representative = metric.keyword in selected_keywords
        ranked_metrics.append(metric.model_copy(update={"included": True}))
        score = _metric_value_score(metric, service, market)
        decisions.append(
            KeywordDecision(
                keyword=metric.keyword,
                canonical_keyword=_normalize_keyword(metric.canonical_keyword),
                decision="representative" if is_representative else "included_scoring",
                reason=_decision_reason(metric, service, market),
                rank=rank,
                representative=is_representative,
                cluster_id=_cluster_id(metric.keyword),
                intent=metric.intent,
                search_volume=metric.search_volume,
                cpc=metric.cpc,
                granularity=metric.market_granularity,
                ranking_score=round(score, 2),
            )
        )

    all_metrics = [*ranked_metrics, *grouped_metrics]
    return KeywordMetricPlan(
        metrics=all_metrics,
        scoring_metrics=ranked_scoring,
        selected_serp_keywords=[metric.keyword for metric in selected],
        clusters=sorted(clusters, key=lambda item: item.representative_keyword),
        decisions=decisions,
    )


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = " ".join(value.lower().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _normalize_keyword(value: str) -> str:
    return " ".join(slugify(value).replace("-", " ").split())


def _matching_negative(canonical: str, negative_terms: list[str]) -> str | None:
    padded = f" {canonical} "
    for term in negative_terms:
        if f" {term} " in padded:
            return term
    return None


def _cluster_key(keyword: str, market: Market) -> str:
    tokens = _cluster_tokens(keyword, market)
    return " ".join(tokens)


def _cluster_tokens(keyword: str, market: Market) -> list[str]:
    local_tokens = _market_tokens(market)
    tokens = []
    for token in _normalize_keyword(keyword).split():
        if token in LOCAL_MODIFIER_TOKENS or token in local_tokens:
            continue
        tokens.append(_singularize(token))
    return tokens


def _market_tokens(market: Market) -> set[str]:
    values = [market.display_name, market.state or "", market.country_code]
    values.extend(market.cities)
    return {token for value in values for token in _normalize_keyword(value).split()}


def _singularize(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _matching_cluster_key(key: str, grouped: dict[str, list[KeywordMetric]]) -> str | None:
    key_tokens = set(key.split())
    for existing in grouped:
        existing_tokens = set(existing.split())
        if key_tokens == existing_tokens:
            return existing
        if key_tokens and existing_tokens and _token_overlap(key_tokens, existing_tokens) >= 0.9:
            return existing
        if SequenceMatcher(None, key, existing).ratio() >= 0.92:
            return existing
    return None


def _token_overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(1, max(len(left), len(right)))


def _metric_value_score(metric: KeywordMetric, service: ServiceFamily, market: Market) -> float:
    canonical = _normalize_keyword(metric.canonical_keyword or metric.keyword)
    tokens = set(canonical.split())
    service_tokens = set(_normalize_keyword(service.display_name).split())
    relevance = len(tokens & service_tokens) / max(1, len(service_tokens))
    intent = 1.0 if metric.intent in TRANSACTIONAL_INTENTS else 0.25
    cpc = min((metric.cpc or 0) / 30, 1.0)
    volume = min((metric.search_volume or 0) / 1000, 1.0)
    local_quality = _local_modifier_quality(canonical, market)
    return relevance * 40 + intent * 25 + cpc * 18 + volume * 12 + local_quality * 5


def _local_modifier_quality(canonical: str, market: Market) -> float:
    tokens = set(canonical.split())
    if {"near", "me"} <= tokens:
        return 0.8
    if tokens & _market_tokens(market):
        return 1.0
    return 0.45


def _decision_reason(metric: KeywordMetric, service: ServiceFamily, market: Market) -> str:
    score = _metric_value_score(metric, service, market)
    return (
        f"value_score:{score:.1f}; intent:{metric.intent}; "
        f"cpc:{metric.cpc or 0}; volume:{metric.search_volume or 0}"
    )


def _cluster_id(keyword: str) -> str:
    return f"kw:{slugify(keyword)}"
