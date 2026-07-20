from pathlib import Path

from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.site_generator.generator import build_site_config, generate_static_site


def test_static_site_artifact_contract_is_complete_and_disclosed(tmp_path: Path) -> None:
    service = ServiceFamily(
        id="water-heater-repair",
        display_name="Water Heater Repair",
        seed_queries=["water heater repair", "water heater installation"],
    )
    market = Market(
        id="st-louis-mo",
        display_name="St. Louis, MO",
        state="MO",
        cities=["St. Louis"],
    )

    output = generate_static_site(build_site_config(service, market), tmp_path)

    required = {
        "index.html",
        "services/index.html",
        "service-area.html",
        "pricing.html",
        "faq.html",
        "about.html",
        "contact.html",
        "privacy.html",
        "terms.html",
        "robots.txt",
        "sitemap.xml",
        "assets/style.css",
    }
    assert required <= {
        str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()
    }
    index = (output / "index.html").read_text()
    assert "independent referral" in index.lower()
    assert "St. Louis, MO" in index
    assert "Water Heater Repair" in index
    assert "/services/water-heater-repair.html" in (output / "sitemap.xml").read_text()
