from pathlib import Path

from rank_rent.domain.models import Market, OutreachDraft, ProviderCandidate, ServiceFamily


def generate_initial_email(
    provider: ProviderCandidate,
    service: ServiceFamily,
    market: Market,
    template_path: Path = Path("config/outreach_templates/initial_email.txt"),
) -> OutreachDraft:
    template = template_path.read_text()
    subject_line, body = template.split("\n\n", 1)
    subject = subject_line.removeprefix("Subject: ").format(
        service=service.display_name, market=market.display_name
    )
    rendered = body.format(
        provider_name=provider.name,
        service=service.display_name.lower(),
        market=market.display_name,
        sender_name="Adam",
    )
    return OutreachDraft(
        provider_name=provider.name,
        type="email",
        subject=subject,
        generated_body=rendered,
        facts_used={
            "provider_name": provider.name,
            "service": service.display_name,
            "market": market.display_name,
            "provider_website": provider.website,
        },
    )

