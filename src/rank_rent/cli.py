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

from rank_rent.db.base import SessionLocal, init_db, reset_db
from rank_rent.db.orm import JsonArtifactORM, OpportunityORM, ScanRunORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOError, DataForSEOLiveProvider
from rank_rent.integrations.dataforseo.replay import DataForSEOReplayProvider
from rank_rent.qualification.report import fixture_capability_report
from rank_rent.replay import (
    ReplayIntegrityError,
    export_responses_for_scan,
    load_response_bundle,
    validate_response_bundle,
)
from rank_rent.repositories import market_from_orm, service_from_orm, upsert_market, upsert_service
from rank_rent.runtime import ConfigurationError, DataMode, validate_runtime_mode
from rank_rent.services.scanner import ScanPipeline, score_summary
from rank_rent.services.seeds import load_markets, load_services
from rank_rent.settings import get_settings
from rank_rent.site_generator.generator import build_site_config, generate_static_site

app = typer.Typer(no_args_is_help=True)
site_app = typer.Typer(no_args_is_help=True)
replay_app = typer.Typer(no_args_is_help=True)
fixtures_app = typer.Typer(no_args_is_help=True)
data_app = typer.Typer(no_args_is_help=True)
app.add_typer(site_app, name="site")
app.add_typer(replay_app, name="replay")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(data_app, name="data")


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


@app.command("reset-db")
def reset_database(confirm: Annotated[bool, typer.Option("--confirm")] = False) -> None:
    if not confirm:
        typer.echo("Pass --confirm to delete all local DB rows and rebuild the current schema.")
        raise typer.Exit(1)
    reset_db()
    typer.echo("Reset database.")


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
        typer.echo(
            f"Opportunity {result['opportunity_id']} ({result['assessment_type']}): "
            f"{score_summary(result['score'])}"
        )
        if result["site_path"]:
            typer.echo(f"Generated site: {result['site_path']}")
        else:
            typer.echo("No site generated during scan; use `rank-rent site generate` after review.")


@replay_app.command("scan")
def replay_scan(scan_run_id: int) -> None:
    init_db()
    require_runtime_mode(DataMode.replay)
    with SessionLocal() as session:
        scan = session.get(ScanRunORM, scan_run_id)
        if scan is None or scan.opportunity_id is None:
            raise typer.BadParameter(f"Scan run {scan_run_id} was not found or has no opportunity.")
        opportunity = session.get(OpportunityORM, scan.opportunity_id)
        if opportunity is None:
            raise typer.BadParameter(f"Opportunity {scan.opportunity_id} was not found.")
        result = asyncio.run(
            ScanPipeline(session, data_mode=DataMode.replay).run(
                service_from_orm(opportunity.service_family),
                market_from_orm(opportunity.market),
                source=f"replay:{scan_run_id}",
                build_site=False,
            )
        )
        typer.echo(json.dumps({"replayed_from_scan_run_id": scan_run_id, "result": result["data_mode"]}))


@replay_app.command("bundle")
def replay_bundle(
    bundle_path: Path,
    service: Annotated[str, typer.Option("--service")],
    market: Annotated[str, typer.Option("--market")],
) -> None:
    init_db()
    require_runtime_mode(DataMode.replay)
    services = {item.id: item for item in load_services()}
    markets = {item.id: item for item in load_markets()}
    selected_service = services.get(service) or ServiceFamily(
        id=service,
        display_name=service.replace("_", " ").title(),
        seed_queries=[service.replace("_", " ")],
    )
    selected_market = markets.get(market) or Market(id=market, display_name=market)
    transport = load_response_bundle(str(bundle_path))
    with SessionLocal() as session:
        result = asyncio.run(
            ScanPipeline(
                session,
                research_provider=DataForSEOReplayProvider(transport),
                data_mode=DataMode.replay,
            ).run(
                selected_service,
                selected_market,
                source=f"bundle:{bundle_path.name}",
                build_site=False,
            )
        )
        typer.echo(
            json.dumps(
                {
                    "bundle": str(bundle_path),
                    "data_mode": result["data_mode"],
                    "scan_id": result["scan_id"],
                    "opportunity_id": result["opportunity_id"],
                    "assessment_type": result["assessment_type"],
                },
                indent=2,
            )
        )


@fixtures_app.command("export")
def fixtures_export(
    scan_run_id: int,
    output: Annotated[Path, typer.Option("--output")] = Path("fixtures/recorded/responses.json"),
) -> None:
    init_db()
    output.parent.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as session:
        try:
            export_responses_for_scan(session, str(output), scan_run_id=scan_run_id)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Exported sanitized stored responses for scan {scan_run_id} to {output}")


@fixtures_app.command("validate")
def fixtures_validate(bundle_path: Path) -> None:
    try:
        result = validate_response_bundle(str(bundle_path))
    except (ReplayIntegrityError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2))


@data_app.command("audit")
def data_audit() -> None:
    from rank_rent.services.data_audit import audit_data

    init_db()
    with SessionLocal() as session:
        typer.echo(json.dumps(audit_data(session), indent=2))


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
