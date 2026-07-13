from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from sqlalchemy import select

from rank_rent.db.base import SessionLocal, init_db
from rank_rent.db.orm import JsonArtifactORM, OpportunityORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOError, DataForSEOLiveProvider
from rank_rent.qualification.report import fixture_capability_report
from rank_rent.repositories import market_from_orm, service_from_orm, upsert_market, upsert_service
from rank_rent.runtime import ConfigurationError, DataMode, validate_runtime_mode
from rank_rent.services.scanner import ScanPipeline, score_summary
from rank_rent.services.seeds import load_markets, load_services
from rank_rent.settings import get_settings
from rank_rent.site_generator.generator import build_site_config, generate_static_site

app = typer.Typer(no_args_is_help=True)
site_app = typer.Typer(no_args_is_help=True)
app.add_typer(site_app, name="site")


def require_runtime_mode(mode: DataMode) -> None:
    try:
        validate_runtime_mode(get_settings(), mode)
    except ConfigurationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(2) from exc


@app.command("init-db")
def init_database() -> None:
    init_db()
    typer.echo("Initialized database.")


@app.command("ingest-seeds")
def ingest_seeds(
    services_path: Path = Path("seeds/services.example.yaml"),
    locations_path: Path = Path("seeds/locations.example.yaml"),
) -> None:
    init_db()
    with SessionLocal() as session:
        for service in load_services(services_path):
            upsert_service(session, service)
        for market in load_markets(locations_path):
            upsert_market(session, market)
        session.commit()
    typer.echo("Seed services and locations ingested.")


@app.command()
def qualify(
    fixtures: Annotated[bool, typer.Option("--fixtures")] = False,
    live: Annotated[bool, typer.Option("--live")] = False,
    service: str = "water_heater_services",
    locations: str = "lower_fairfield_county",
) -> None:
    if live:
        require_runtime_mode(DataMode.live)
        try:
            provider = DataForSEOLiveProvider(settings=get_settings())
            account = asyncio.run(provider.check_account())
            location = asyncio.run(provider.resolve_location("Stamford, CT"))
        except DataForSEOError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(2) from exc
        typer.echo(
            json.dumps(
                {
                    "data_mode": DataMode.live.value,
                    "account_status": account["status_message"],
                    "test_location": location.model_dump(mode="json"),
                    "paid_scan_calls_enabled": get_settings().allow_live_api_calls,
                },
                indent=2,
            )
        )
        return
    if not fixtures:
        fixtures = True
    require_runtime_mode(DataMode.fixture)
    init_db()
    services = {item.id: item for item in load_services()}
    markets = {item.id: item for item in load_markets()}
    selected_service = services[service]
    selected_market = markets[locations]
    with SessionLocal() as session:
        result = asyncio.run(
            ScanPipeline(session, data_mode=DataMode.fixture).run(
                selected_service,
                selected_market,
                source="fixture",
            )
        )
        report = fixture_capability_report(result)
        Path("fixtures/expected/capability_report.json").write_text(json.dumps(report, indent=2))
        typer.echo(score_summary(result["score"]))
        typer.echo(json.dumps(report, indent=2))


@app.command()
def scan(
    service: Annotated[str, typer.Option("--service")],
    market: Annotated[str, typer.Option("--market")],
    data_mode: Annotated[DataMode, typer.Option("--data-mode")] = DataMode.fixture,
) -> None:
    init_db()
    require_runtime_mode(data_mode)
    services = {item.id: item for item in load_services()}
    markets = {item.id: item for item in load_markets()}
    selected_service = services.get(service) or ServiceFamily(
        id=service,
        display_name=service.replace("_", " ").title(),
        seed_queries=[service.replace("_", " ")],
    )
    selected_market = markets.get(market) or Market(id=market, display_name=market)
    with SessionLocal() as session:
        result = asyncio.run(
            ScanPipeline(session, data_mode=data_mode).run(selected_service, selected_market)
        )
        typer.echo(f"Opportunity {result['opportunity_id']}: {score_summary(result['score'])}")
        typer.echo(f"Generated site: {result['site_path']}")


@site_app.command("generate")
def site_generate(opportunity_id: int) -> None:
    init_db()
    with SessionLocal() as session:
        opportunity = session.get(OpportunityORM, opportunity_id)
        if opportunity is None:
            raise typer.BadParameter(f"Opportunity {opportunity_id} not found")
        service = service_from_orm(opportunity.service_family)
        market = market_from_orm(opportunity.market)
        domains = session.scalars(
            select(JsonArtifactORM)
            .where(JsonArtifactORM.opportunity_id == opportunity_id)
            .where(JsonArtifactORM.kind == "domain_candidates")
            .order_by(JsonArtifactORM.id.desc())
        ).first()
        domain = None
        if domains and domains.payload.get("domains"):
            domain = domains.payload["domains"][0]["domain"]
        path = generate_static_site(build_site_config(service, market, domain))
        typer.echo(str(path))


@site_app.command("preview")
def site_preview(opportunity_id: int, port: int = 8008) -> None:
    site_generate(opportunity_id)
    with SessionLocal() as session:
        opportunity = session.get(OpportunityORM, opportunity_id)
        if opportunity is None:
            raise typer.BadParameter(f"Opportunity {opportunity_id} not found")
        path = generate_static_site(
            build_site_config(service_from_orm(opportunity.service_family), market_from_orm(opportunity.market))
        )
    typer.echo(f"Serving {path} at http://127.0.0.1:{port}")
    subprocess.run([sys.executable, "-m", "http.server", str(port), "-d", str(path)], check=False)


@site_app.command("deploy-staging")
def site_deploy_staging(opportunity_id: int, confirm: bool = False) -> None:
    if not confirm:
        typer.echo("Pass --confirm to deploy the configured staging sample.")
        raise typer.Exit(1)
    from rank_rent.integrations.deployment.local import LocalStagingDeploymentProvider

    with SessionLocal() as session:
        opportunity = session.get(OpportunityORM, opportunity_id)
        if opportunity is None:
            raise typer.BadParameter(f"Opportunity {opportunity_id} not found")
        path = generate_static_site(
            build_site_config(service_from_orm(opportunity.service_family), market_from_orm(opportunity.market))
        )
        result = asyncio.run(
            LocalStagingDeploymentProvider().deploy_staging(path, f"opportunity-{opportunity_id}")
        )
        typer.echo(result.model_dump_json(indent=2))


@app.command()
def web(host: str = "127.0.0.1", port: int = 8000) -> None:
    init_db()
    uvicorn.run("rank_rent.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
