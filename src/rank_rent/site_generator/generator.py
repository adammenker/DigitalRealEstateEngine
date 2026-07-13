from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from rank_rent.domain.models import Asset, Market, ServiceFamily, SiteConfig, slugify

DISCLOSURE = (
    "This independent referral website does not perform contractor services directly. "
    "Inquiries may be connected with an independent local service provider after manual review."
)


def build_site_config(
    service: ServiceFamily,
    market: Market,
    domain_candidate: str | None = None,
) -> SiteConfig:
    brand = f"{market.cities[0] if market.cities else market.display_name} {service.display_name} Guide"
    services = service.seed_queries[:4] or [service.display_name]
    return SiteConfig(
        property_brand=brand,
        domain_candidate=domain_candidate,
        service_family=service,
        market=market,
        service_area_display_text=market.display_name,
        contact_disclosure=DISCLOSURE,
        services=[s.title() for s in services],
        faqs=[
            {
                "question": f"Can I request help with {service.display_name.lower()}?",
                "answer": "Yes. The form collects your request so it can be manually reviewed and, when appropriate, shared with an independent local provider.",
            },
            {
                "question": "Is this website a contractor?",
                "answer": "No. It is an independent referral and information property, not a licensed service provider.",
            },
        ],
        pricing_guidance=(
            "Pricing varies by job scope, timing, materials, property access, and local provider policies. "
            "Request provider-specific estimates before approving any work."
        ),
        images=[
            Asset(
                type="placeholder",
                alt_text=f"{service.display_name} service area placeholder image",
                approved=True,
            )
        ],
        metadata={
            "title": f"{brand} | Independent Local Referral Resource",
            "description": f"Independent guide for {service.display_name.lower()} around {market.display_name}.",
        },
        legal_disclosure_content=DISCLOSURE,
    )


def _pages(config: SiteConfig) -> dict[str, dict[str, str]]:
    service_pages = {
        f"services/{slugify(service)}.html": {
            "title": service,
            "body": f"Information and request intake for {service.lower()} in {config.service_area_display_text}.",
        }
        for service in config.services
    }
    pages = {
        "index.html": {
            "title": config.property_brand,
            "body": f"Independent local referral resource for {config.service_family.display_name.lower()} around {config.service_area_display_text}.",
        },
        "services/index.html": {
            "title": f"{config.service_family.display_name} Overview",
            "body": "Review common service needs and submit a request for manual review.",
        },
        "service-area.html": {
            "title": "Service Area",
            "body": f"Focused on {config.service_area_display_text}.",
        },
        "pricing.html": {"title": "Pricing Guidance", "body": config.pricing_guidance},
        "faq.html": {"title": "FAQ", "body": "Common questions about this independent referral resource."},
        "about.html": {"title": "About", "body": config.contact_disclosure},
        "contact.html": {"title": "Contact", "body": "Use the form to request manual review."},
        "privacy.html": {"title": "Privacy", "body": "Do not submit sensitive information through this sample site."},
        "terms.html": {"title": "Terms", "body": config.legal_disclosure_content},
    }
    pages.update(service_pages)
    return pages


def generate_static_site(config: SiteConfig, output_root: Path = Path("generated_sites")) -> Path:
    slug = slugify(config.property_brand)
    output_dir = output_root / slug
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("page.html")
    css_dir = output_dir / "assets"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "style.css").write_text(STYLE)
    pages = _pages(config)
    for path, page in pages.items():
        destination = output_dir / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(template.render(config=config, page=page, pages=pages))
    (output_dir / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n")
    sitemap = "\n".join(f"<url><loc>/{path}</loc></url>" for path in pages)
    (output_dir / "sitemap.xml").write_text(f'<?xml version="1.0"?><urlset>{sitemap}</urlset>')
    return output_dir


STYLE = """
:root{color-scheme:light;--ink:#16201c;--muted:#5d6a64;--line:#d8e0dc;--brand:#146c5f;--wash:#f4f8f6;--accent:#b0413e}
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:var(--ink);background:white;line-height:1.55}
a{color:var(--brand)}header{border-bottom:1px solid var(--line);background:var(--wash)}nav{max-width:1080px;margin:auto;display:flex;gap:18px;align-items:center;padding:16px 20px;flex-wrap:wrap}
nav strong{margin-right:auto}.hero{max-width:1080px;margin:auto;padding:56px 20px 44px}.hero h1{font-size:clamp(2rem,4vw,3.75rem);line-height:1.05;margin:0 0 16px}.hero p{max-width:720px;font-size:1.1rem;color:var(--muted)}
main{max-width:1080px;margin:auto;padding:32px 20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}.card{border:1px solid var(--line);border-radius:8px;padding:18px;background:white}
.notice{border-left:4px solid var(--accent);background:#fff8f6;padding:14px 16px;margin:24px 0}form{display:grid;gap:12px;max-width:560px}input,textarea,button{font:inherit;padding:10px;border:1px solid var(--line);border-radius:6px}button{background:var(--brand);color:white;border:0;cursor:pointer}footer{border-top:1px solid var(--line);margin-top:40px;padding:24px 20px;color:var(--muted)}
""".strip()

