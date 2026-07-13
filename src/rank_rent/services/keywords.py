from rank_rent.domain.models import KeywordCandidate, slugify


def dedupe_and_filter_keywords(
    candidates: list[KeywordCandidate], negative_terms: list[str]
) -> list[KeywordCandidate]:
    seen: set[str] = set()
    output: list[KeywordCandidate] = []
    negatives = [term.lower() for term in negative_terms]
    for candidate in candidates:
        normalized = slugify(candidate.keyword).replace("-", " ")
        if normalized in seen:
            continue
        seen.add(normalized)
        if any(term in normalized for term in negatives):
            output.append(
                candidate.model_copy(update={"included": False, "excluded_reason": "negative_term"})
            )
        else:
            output.append(candidate)
    return output

