from __future__ import annotations

import asyncio
import getpass
import json
import signal
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
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
from rank_rent.lead_routing.adapters import (
    FixtureDeliveryAdapter,
    FixtureOperatorAlertAdapter,
)
from rank_rent.lead_routing.models import DeliveryChannel
from rank_rent.lead_routing.worker import run_lead_delivery_runtime
from rank_rent.qualification.report import fixture_capability_report
from rank_rent.replay import (
    ReplayIntegrityError,
    export_responses_for_scan,
    load_response_bundle,
    validate_response_bundle,
)
from rank_rent.repositories import market_from_orm, service_from_orm, upsert_market, upsert_service
from rank_rent.runtime import ConfigurationError, DataMode, validate_runtime_mode
from rank_rent.services.billing import reconcile_billing_csv
from rank_rent.services.cost_controls import (
    daily_usage,
    evaluate_alerts,
    resolve_unknown_provider_call,
)
from rank_rent.services.dataforseo_qualification import run_dataforseo_qualification
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.qualification import (
    DATAFORSEO_ADAPTER_VERSION,
    current_qualification,
    record_qualification,
)
from rank_rent.services.scan_worker import run_worker_runtime
from rank_rent.services.scanner import ScanPipeline, score_summary
from rank_rent.services.seeds import load_markets, load_services
from rank_rent.services.us_geography import USGeographyIndex
from rank_rent.settings import get_settings
from rank_rent.site_generator.generator import build_site_config, generate_static_site

app = typer.Typer(no_args_is_help=True)
site_app = typer.Typer(no_args_is_help=True)
replay_app = typer.Typer(no_args_is_help=True)
fixtures_app = typer.Typer(no_args_is_help=True)
data_app = typer.Typer(no_args_is_help=True)
calibrate_app = typer.Typer(no_args_is_help=True)
qualification_app = typer.Typer(no_args_is_help=True)
billing_app = typer.Typer(no_args_is_help=True)
app.add_typer(site_app, name="site")
app.add_typer(replay_app, name="replay")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(data_app, name="data")
app.add_typer(calibrate_app, name="calibrate")
app.add_typer(qualification_app, name="qualification")
app.add_typer(billing_app, name="billing")


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
        init_db()
        try:
            with SessionLocal() as session:
                provider = DataForSEOLiveProvider(
                    settings=get_settings(),
                    session=session,
                    allow_unplanned_requests=True,
                )
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
                    "production_qualification_recorded": False,
                    "qualification_note": (
                        "This smoke check covers account access and location lookup only. "
                        "Record the complete matrix with `rank-rent qualification record`."
                    ),
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
        typer.echo(
            json.dumps({"replayed_from_scan_run_id": scan_run_id, "result": result["data_mode"]})
        )


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


@calibrate_app.command("run")
def calibrate_run(
    scoring_version: Annotated[
        str | None,
        typer.Option("--scoring-version", help="Version declared in the benchmark manifest."),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Immutable historical report directory."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
    save: Annotated[
        bool,
        typer.Option("--save/--no-save", help="Preserve the report as an immutable artifact."),
    ] = True,
) -> None:
    from rank_rent.calibration.loader import CalibrationConfigError, project_path
    from rank_rent.calibration.reporting import render_report, save_report
    from rank_rent.calibration.runner import CalibrationRunner

    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json")
    try:
        runner = CalibrationRunner(get_settings().project_root)
        report = runner.run(scoring_version)
        typer.echo(render_report(report, output_format=output_format))
        if save:
            destination = output_dir or project_path(
                runner.project_root,
                runner.manifest.reports_directory,
            )
            typer.echo(f"Saved report: {save_report(report, destination)}")
    except (CalibrationConfigError, OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(2) from exc
    if not report.success:
        raise typer.Exit(1)


@calibrate_app.command("report")
def calibrate_report(
    report_path: Annotated[
        Path | None,
        typer.Argument(help="Historical report path; defaults to the latest report."),
    ] = None,
    reports_dir: Annotated[
        Path | None,
        typer.Option("--reports-dir", help="Directory searched for the latest report."),
    ] = None,
    output_format: Annotated[str, typer.Option("--format")] = "text",
) -> None:
    from rank_rent.calibration.loader import project_path
    from rank_rent.calibration.reporting import latest_report, load_report, render_report
    from rank_rent.calibration.runner import CalibrationRunner

    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json")
    try:
        runner = CalibrationRunner(get_settings().project_root)
        path = report_path or latest_report(
            reports_dir
            or project_path(runner.project_root, runner.manifest.reports_directory)
        )
        typer.echo(render_report(load_report(path), output_format=output_format))
    except (FileNotFoundError, OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(2) from exc


@calibrate_app.command("compare")
def calibrate_compare(
    scoring_version_a: Annotated[str, typer.Argument()],
    scoring_version_b: Annotated[str, typer.Argument()],
    output_format: Annotated[str, typer.Option("--format")] = "text",
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Optional immutable comparison report directory."),
    ] = None,
) -> None:
    from rank_rent.calibration.loader import CalibrationConfigError
    from rank_rent.calibration.reporting import render_comparison, save_report
    from rank_rent.calibration.runner import CalibrationRunner

    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json")
    try:
        report = CalibrationRunner(get_settings().project_root).compare(
            scoring_version_a,
            scoring_version_b,
        )
        typer.echo(render_comparison(report, output_format=output_format))
        if output_dir is not None:
            typer.echo(f"Saved comparison: {save_report(report, output_dir)}")
    except (CalibrationConfigError, OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(2) from exc


@calibrate_app.command("validate-config")
def calibrate_validate_config(
    output_format: Annotated[str, typer.Option("--format")] = "text",
) -> None:
    from rank_rent.calibration.loader import CalibrationConfigError
    from rank_rent.calibration.runner import CalibrationRunner

    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json")
    try:
        report = CalibrationRunner(get_settings().project_root).validate_config()
    except (CalibrationConfigError, OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(2) from exc
    if output_format == "json":
        typer.echo(report.model_dump_json(indent=2))
    else:
        typer.echo(
            f"Calibration configuration: {'VALID' if report.valid else 'INVALID'}\n"
            f"Suite: {report.suite_version}\n"
            f"Scenarios: {report.scenario_count}\n"
            f"Scoring versions: {', '.join(report.scoring_versions)}\n"
            f"Configuration hash: {report.benchmark_config_hash}"
        )
        for check in report.checks:
            typer.echo(f"[{'PASS' if check.passed else 'FAIL'}] {check.check}")
    if not report.valid:
        raise typer.Exit(1)


@data_app.command("usage")
def data_usage() -> None:
    init_db()
    settings = get_settings()
    provider = (
        "dataforseo-live"
        if settings.dataforseo_environment.strip().lower() == "production"
        else "dataforseo-sandbox"
    )
    with SessionLocal() as session:
        today = datetime.now(UTC).date()
        payload = daily_usage(session, provider=provider, usage_date=today)
        payload["alerts"] = evaluate_alerts(
            session,
            settings=settings,
            provider=provider,
            usage_date=today,
        )
        typer.echo(json.dumps(payload, indent=2))


@data_app.command("resolve-unknown-call")
def data_resolve_unknown_call(
    api_call_id: int,
    outcome: str,
    actual_cost_usd: float,
    reason: Annotated[str, typer.Option("--reason")],
) -> None:
    """Reconcile an ambiguous provider attempt without resending it."""
    init_db()
    with SessionLocal() as session:
        row = resolve_unknown_provider_call(
            session,
            api_call_id=api_call_id,
            outcome=outcome,
            actual_cost_usd=actual_cost_usd,
            resolution_note=reason,
        )
    typer.echo(
        json.dumps(
            {
                "api_call_id": row.id,
                "status": row.status,
                "provider_outcome": row.provider_outcome,
                "actual_cost_usd": row.actual_cost_usd,
                "reconciled_at": row.reconciled_at.isoformat() if row.reconciled_at else None,
            },
            indent=2,
        )
    )


@qualification_app.command("record")
def qualification_record(
    results_path: Path,
    override_reason: Annotated[str, typer.Option("--reason")],
    notes: str = "",
) -> None:
    """Import audited manual results; imports never unlock production paid calls."""
    init_db()
    settings = get_settings()
    checks = json.loads(results_path.read_text())
    if not isinstance(checks, dict):
        raise typer.BadParameter("Qualification results must be a JSON object keyed by check name.")
    environment = settings.dataforseo_environment.strip().lower()
    provider = "dataforseo-live" if environment == "production" else "dataforseo-sandbox"
    with SessionLocal() as session:
        row = record_qualification(
            session,
            provider=provider,
            environment=environment,
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks=checks,
            ttl_hours=settings.qualification_ttl_hours,
            notes=notes,
            executed_by=getpass.getuser(),
            override_reason=override_reason,
        )
    typer.echo(
        json.dumps(
            {
                "status": row.status,
                "adapter_version": row.adapter_version,
                "qualified_at": row.qualified_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "execution_method": row.execution_method,
                "gate_eligible": row.gate_eligible,
                "warning": "Manual qualification records do not unlock production paid calls.",
            },
            indent=2,
        )
    )


@qualification_app.command("run")
def qualification_run(notes: str = "") -> None:
    """Execute the production qualification matrix and persist hashed evidence."""
    init_db()
    settings = get_settings()
    require_runtime_mode(DataMode.live)
    service = ServiceFamily(
        id="plumbing",
        display_name="Plumbing Services",
        seed_queries=["plumber", "plumbing repair"],
        provider_categories=["plumber"],
    )
    matches = USGeographyIndex.from_settings(settings).search("St. Louis, MO", limit=1)
    if not matches:
        raise typer.BadParameter(
            "The offline geography index could not resolve the qualification market."
        )
    market = market_from_geography_record(matches[0].record)
    with SessionLocal() as session:
        row = asyncio.run(
            run_dataforseo_qualification(
                session,
                settings=settings,
                service=service,
                market=market,
                executed_by=getpass.getuser(),
                notes=notes,
            )
        )
    typer.echo(
        json.dumps(
            {
                "status": row.status,
                "gate_eligible": row.gate_eligible,
                "evidence_sha256": row.evidence_sha256,
                "adapter_version": row.adapter_version,
                "qualified_at": row.qualified_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
            },
            indent=2,
        )
    )


@qualification_app.command("status")
def qualification_status() -> None:
    init_db()
    settings = get_settings()
    environment = settings.dataforseo_environment.strip().lower()
    provider = "dataforseo-live" if environment == "production" else "dataforseo-sandbox"
    with SessionLocal() as session:
        row = current_qualification(
            session,
            provider=provider,
            environment=environment,
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
        )
    typer.echo(
        json.dumps(
            {
                "current": row is not None,
                "provider": provider,
                "environment": environment,
                "adapter_version": DATAFORSEO_ADAPTER_VERSION,
                "expires_at": row.expires_at.isoformat() if row else None,
                "execution_method": row.execution_method if row else None,
                "evidence_sha256": row.evidence_sha256 if row else None,
            },
            indent=2,
        )
    )


@billing_app.command("reconcile")
def billing_reconcile(csv_path: Path) -> None:
    init_db()
    settings = get_settings()
    environment = settings.dataforseo_environment.strip().lower()
    provider = "dataforseo-live" if environment == "production" else "dataforseo-sandbox"
    with SessionLocal() as session:
        report = reconcile_billing_csv(
            session,
            csv_path,
            provider=provider,
            environment=environment,
            tolerance_usd=settings.billing_reconciliation_tolerance_usd,
        )
    typer.echo(json.dumps(report, indent=2))


@app.command("worker")
def worker(
    concurrency: Annotated[int | None, typer.Option("--concurrency", min=1, max=32)] = None,
) -> None:
    """Run the durable scan worker as a process separate from the API server."""
    init_db()
    settings = get_settings()

    async def serve() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_name, stop_event.set)
        await run_worker_runtime(
            stop_event,
            concurrency=concurrency or settings.scan_worker_concurrency,
            settings=settings,
        )

    asyncio.run(serve())


@app.command("lead-worker")
def lead_worker(
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=32)] = 1,
) -> None:
    """Run durable lead delivery with non-network fixture adapters."""
    init_db()
    settings = get_settings()
    if settings.app_env.strip().lower() == "production":
        raise typer.BadParameter(
            "Fixture lead adapters are prohibited in production. "
            "Configure reviewed provider adapters before starting this worker."
        )

    async def serve() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_name, stop_event.set)
        await run_lead_delivery_runtime(
            stop_event,
            concurrency=concurrency,
            adapters={
                DeliveryChannel.email: FixtureDeliveryAdapter("email"),
                DeliveryChannel.phone: FixtureDeliveryAdapter("phone"),
            },
            alert_adapter=FixtureOperatorAlertAdapter(),
            poll_seconds=settings.scan_worker_poll_seconds,
            heartbeat_seconds=settings.scan_worker_heartbeat_seconds,
            stale_after_seconds=settings.scan_worker_stale_after_seconds,
        )

    asyncio.run(serve())


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
            build_site_config(
                service_from_orm(opportunity.service_family), market_from_orm(opportunity.market)
            )
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
            build_site_config(
                service_from_orm(opportunity.service_family), market_from_orm(opportunity.market)
            )
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
