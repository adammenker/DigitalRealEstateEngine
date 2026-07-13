from urllib.parse import urlparse

from rank_rent.domain.models import SerpResult

DIRECTORIES = {"yelp.com", "angi.com", "homeadvisor.com", "thumbtack.com"}
NATIONAL_BRANDS = {"homedepot.com", "lowes.com", "angi.com"}
MARKETPLACES = {"thumbtack.com"}
LEAD_GEN_HINTS = ("lead", "quote", "referral", "near-me")


def classify_result(result: SerpResult) -> SerpResult:
    domain = result.domain.lower().removeprefix("www.")
    path = urlparse(result.url).path.lower()
    text = f"{result.title} {result.description} {path}".lower()

    classification = "unknown"
    if any(domain == d or domain.endswith(f".{d}") for d in DIRECTORIES):
        classification = "directory"
    elif any(domain == d or domain.endswith(f".{d}") for d in NATIONAL_BRANDS):
        classification = "national_brand"
    elif any(domain == d or domain.endswith(f".{d}") for d in MARKETPLACES):
        classification = "marketplace"
    elif any(hint in text for hint in LEAD_GEN_HINTS):
        classification = "lead_generation"
    elif any(word in text for word in ["repair", "service", "installation", "contractor", "pros"]):
        classification = "local_provider"

    return result.model_copy(
        update={
            "classification": classification,
            "is_local_provider": classification == "local_provider",
            "is_directory": classification == "directory",
            "is_national_brand": classification == "national_brand",
            "is_lead_generation_site": classification == "lead_generation",
        }
    )

