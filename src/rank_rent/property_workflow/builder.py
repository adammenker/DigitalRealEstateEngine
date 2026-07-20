from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from xml.etree import ElementTree

from rank_rent.property_workflow.models import BuildEnvironment

BUILDER_VERSION = "deterministic-static-v1"
MAX_TOTAL_BYTES = 1_000_000
MAX_HTML_BYTES = 200_000
DISCLOSURE_MARKER = "independent referral"


@dataclass(frozen=True)
class BuildArtifact:
    output_path: Path
    checksum: str
    manifest: dict[str, str]
    validation: dict[str, object]
    total_bytes: int


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "property"


def _page(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    brand: str,
    disclosure: str,
    robots: str,
    service_name: str,
    market_name: str,
) -> str:
    structured = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": brand,
            "description": description,
            "about": {
                "@type": "Service",
                "name": service_name,
                "areaServed": market_name,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="{robots}">
  <title>{html.escape(title)}</title>
  <meta name="description" content="{html.escape(description, quote=True)}">
  <link rel="canonical" href="{html.escape(canonical, quote=True)}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{html.escape(title, quote=True)}">
  <meta property="og:description" content="{html.escape(description, quote=True)}">
  <meta property="og:url" content="{html.escape(canonical, quote=True)}">
  <link rel="stylesheet" href="/assets/site.css">
  <script type="application/ld+json">{structured}</script>
</head>
<body>
  <header><nav aria-label="Primary"><a class="brand" href="/">{html.escape(brand)}</a>
    <a href="/services.html">Services</a><a href="/faq.html">FAQ</a>
    <a href="/contact.html">Contact</a></nav></header>
  <main>{body}</main>
  <footer><strong>Referral disclosure</strong><p>{html.escape(disclosure)}</p>
    <nav aria-label="Legal"><a href="/privacy.html">Privacy</a>
    <a href="/terms.html">Terms</a></nav></footer>
</body>
</html>
"""


STYLE = """\
:root{--ink:#17211d;--muted:#55635d;--line:#d8dfdc;--brand:#11645a;--accent:#d3543c;--wash:#f2f7f5}
*{box-sizing:border-box}body{margin:0;color:var(--ink);font-family:system-ui,sans-serif;line-height:1.6}
a{color:var(--brand)}header{border-bottom:1px solid var(--line)}nav{display:flex;gap:1.25rem;align-items:center;max-width:70rem;margin:auto;padding:1rem}.brand{margin-right:auto;font-weight:750}
main{max-width:70rem;margin:auto;padding:clamp(2rem,7vw,5rem) 1rem}h1{font-size:clamp(2rem,5vw,4.5rem);line-height:1.05;max-width:15ch}h2{margin-top:2rem}.lead{font-size:1.2rem;color:var(--muted);max-width:48rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:1rem}.panel{border:1px solid var(--line);border-radius:8px;padding:1.25rem;background:#fff}.cta{display:inline-block;background:var(--brand);color:#fff;padding:.7rem 1rem;border-radius:6px}
footer{background:var(--wash);border-top:3px solid var(--accent);padding:1.5rem max(1rem,calc((100% - 68rem)/2))}footer nav{padding:0;margin:0}
label{display:block;margin:.7rem 0}.field{display:block;width:100%;max-width:35rem;padding:.7rem;border:1px solid var(--line)}
@media(max-width:34rem){header nav{align-items:flex-start;flex-direction:column}.brand{margin-right:0}}
"""


def build_static_site(
    payload: dict[str, object],
    *,
    domain: str | None,
    environment: BuildEnvironment,
    output_root: Path,
) -> BuildArtifact:
    brand_config = cast(dict[str, Any], payload["brand"])
    brand = str(brand_config.get("name", "")).strip()
    service = cast(dict[str, Any], payload["service"])
    market = cast(dict[str, Any], payload["market"])
    metadata = cast(dict[str, Any], payload["metadata"])
    disclosure = str(payload["referral_disclosure"])
    service_name = str(service.get("name", "")).strip()
    market_name = str(market.get("name", "")).strip()
    description = str(metadata["description"])
    base_url = f"https://{domain}" if domain else "https://preview.invalid"
    robots_meta = (
        "index,follow"
        if environment == BuildEnvironment.production
        else "noindex,nofollow"
    )
    cta = cast(list[dict[str, Any]], payload["calls_to_action"])[0]
    process = "".join(
        f"<article class=\"panel\"><h2>{html.escape(str(item.get('title', 'Step')))}</h2>"
        f"<p>{html.escape(str(item.get('description', '')))}</p></article>"
        for item in cast(list[dict[str, Any]], payload["service_process"])
    )
    considerations = "".join(
        f"<article class=\"panel\"><h2>{html.escape(str(item.get('title', 'Local note')))}</h2>"
        f"<p>{html.escape(str(item.get('description', '')))}</p></article>"
        for item in cast(list[dict[str, Any]], payload["local_considerations"])
    )
    faq = "".join(
        f"<article class=\"panel\"><h2>{html.escape(str(item['question']))}</h2>"
        f"<p>{html.escape(str(item['answer']))}</p></article>"
        for item in cast(list[dict[str, str]], payload["faqs"])
    )
    pages = {
        "index.html": (
            str(metadata["title"]),
            f"<h1>{html.escape(brand)}</h1><p class=\"lead\">Independent information and "
            f"referral support for {html.escape(service_name)} in {html.escape(market_name)}."
            f"</p><a class=\"cta\" href=\"/contact.html\">{html.escape(str(cta.get('label', 'Request help')))}</a>"
            "<section class=\"panel\" data-active-provider hidden><h2>Current independent "
            "provider</h2><strong></strong></section>"
            f"<section class=\"grid\">{considerations}</section>"
            '<script src="/assets/provider.js" defer></script>',
        ),
        "services.html": (
            f"{service_name} | {brand}",
            f"<h1>{html.escape(service_name)}</h1><p class=\"lead\">"
            f"{html.escape(str(service.get('summary', 'Service information and request intake.')))}</p>"
            f"<section class=\"grid\">{process}</section>",
        ),
        "faq.html": (f"Questions | {brand}", f"<h1>Common questions</h1><section class=\"grid\">{faq}</section>"),
        "contact.html": (
            f"Request help | {brand}",
            "<h1>Request help</h1><p class=\"lead\">Requests are reviewed before they may be "
            "shared with an independent provider.</p><form><label>Name<input class=\"field\" "
            "name=\"name\" autocomplete=\"name\"></label><label>Contact information<input "
            "class=\"field\" name=\"contact\"></label><label>Request<textarea class=\"field\" "
            "name=\"message\" rows=\"5\"></textarea></label></form>",
        ),
        "privacy.html": (
            f"Privacy | {brand}",
            "<h1>Privacy</h1><p>Only information needed to review and route a request should be submitted.</p>",
        ),
        "terms.html": (
            f"Terms | {brand}",
            "<h1>Terms</h1><p>This property provides information and referral intake. "
            "Providers remain responsible for their own estimates, work, credentials, and agreements.</p>",
        ),
    }
    build_key = hashlib.sha256(
        json.dumps(
            {"payload": payload, "domain": domain, "environment": environment.value},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    output = output_root / build_key
    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True)
    (output / "assets/site.css").write_text(STYLE, encoding="utf-8")
    (output / "assets/provider.js").write_text(
        """\
fetch("/provider-config.json").then((response)=>response.ok?response.json():null).then((provider)=>{
  if(!provider||!provider.public_business_name)return;const root=document.querySelector("[data-active-provider]");
  if(!root)return;root.hidden=false;root.querySelector("strong").textContent=provider.public_business_name;
}).catch(()=>{});
""",
        encoding="utf-8",
    )
    for filename, (title, body) in pages.items():
        route = "/" if filename == "index.html" else f"/{filename}"
        (output / filename).write_text(
            _page(
                title=title,
                description=description,
                canonical=f"{base_url}{route}",
                body=body,
                brand=brand,
                disclosure=disclosure,
                robots=robots_meta,
                service_name=service_name,
                market_name=market_name,
            ),
            encoding="utf-8",
        )
    sitemap_urls = "".join(
        f"<url><loc>{base_url}{'/' if name == 'index.html' else f'/{name}'}</loc></url>"
        for name in sorted(pages)
    )
    (output / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{sitemap_urls}</urlset>",
        encoding="utf-8",
    )
    robots = (
        f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n"
        if environment == BuildEnvironment.production
        else "User-agent: *\nDisallow: /\n"
    )
    (output / "robots.txt").write_text(robots, encoding="utf-8")
    (output / "asset-provenance.json").write_text(
        json.dumps(
            payload.get("asset_provenance", []),
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    manifest: dict[str, str] = {}
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        relative = path.relative_to(output).as_posix()
        manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    checksum = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    validation = validate_build(output, environment)
    total_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    return BuildArtifact(output, checksum, manifest, validation, total_bytes)


def validate_build(
    output: Path,
    environment: BuildEnvironment,
) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    html_files = sorted(path for path in output.rglob("*.html"))
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        if "<html lang=" not in lowered:
            errors.append(f"{path.name}:missing_html_language")
        if "<h1" not in lowered or "<title>" not in lowered:
            errors.append(f"{path.name}:missing_title_or_h1")
        if DISCLOSURE_MARKER not in lowered:
            errors.append(f"{path.name}:missing_visible_referral_disclosure")
        if 'rel="canonical"' not in lowered or 'property="og:' not in lowered:
            errors.append(f"{path.name}:missing_canonical_or_open_graph")
        if '"@type":"localbusiness"' in lowered:
            errors.append(f"{path.name}:untruthful_local_business_schema")
        if path.stat().st_size > MAX_HTML_BYTES:
            errors.append(f"{path.name}:html_performance_budget_exceeded")
        if environment != BuildEnvironment.production and "noindex,nofollow" not in lowered:
            errors.append(f"{path.name}:staging_must_be_noindex")
        for href in re.findall(r'href="([^"]+)"', text):
            parsed = urlparse(href)
            if parsed.scheme or href.startswith("#"):
                continue
            target = href.lstrip("/") or "index.html"
            if target.endswith("/"):
                target += "index.html"
            if target not in files:
                errors.append(f"{path.name}:broken_internal_link:{href}")
    try:
        ElementTree.parse(output / "sitemap.xml")
    except (ElementTree.ParseError, OSError):
        errors.append("invalid_sitemap_xml")
    robots = (output / "robots.txt").read_text(encoding="utf-8")
    if environment == BuildEnvironment.production:
        if "Disallow: /" in robots:
            errors.append("production_robots_disallows_indexing")
    elif "Disallow: /" not in robots:
        errors.append("nonproduction_robots_allows_indexing")
    total_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    if total_bytes > MAX_TOTAL_BYTES:
        errors.append("total_performance_budget_exceeded")
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "valid_html_structure": not any("missing_" in error for error in errors),
            "accessible_baseline": not any("language" in error for error in errors),
            "sitemap_valid": "invalid_sitemap_xml" not in errors,
            "internal_links_valid": not any("broken_internal_link" in error for error in errors),
            "performance_budget": not any("budget_exceeded" in error for error in errors),
            "referral_disclosure_visible": not any(
                "referral_disclosure" in error for error in errors
            ),
            "truthful_structured_data": not any(
                "local_business_schema" in error for error in errors
            ),
            "indexing_policy_valid": not any(
                "robots" in error or "noindex" in error for error in errors
            ),
        },
    }
