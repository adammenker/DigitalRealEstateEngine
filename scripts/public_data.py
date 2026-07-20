#!/usr/bin/env python3
"""Stage, validate, activate, inspect, and roll back public-data releases."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from rank_rent.public_data.adapters import (
    ACSAdapter,
    CBPAdapter,
    FilePublicDataTransport,
    NESAdapter,
    OfflineFixtureAdapter,
    PublicDataAdapter,
)
from rank_rent.public_data.catalog import load_dataset_catalog
from rank_rent.public_data.models import DatasetKind, DatasetRelease
from rank_rent.public_data.store import PublicDataStore

ROOT = Path(__file__).parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        type=Path,
        default=ROOT / "data" / "public_data",
        help="Public-data store root.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "config" / "public_data" / "datasets.yaml",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    for command in ("stage", "refresh"):
        ingest = commands.add_parser(command)
        ingest.add_argument(
            "--dataset", choices=[item.value for item in DatasetKind], required=True
        )
        ingest.add_argument("--version", required=True)
        ingest.add_argument("--data-year", type=int, required=True)
        ingest.add_argument("--release-date", type=date.fromisoformat, required=True)
        source_group = ingest.add_mutually_exclusive_group()
        source_group.add_argument(
            "--source",
            type=Path,
            help="Previously normalized JSON/JSONL records (legacy fixture path).",
        )
        source_group.add_argument(
            "--official-source",
            type=Path,
            help="Previously downloaded official Census API JSON or CSV.",
        )
        ingest.add_argument(
            "--source-url",
            help="Override the authoritative URL from the dataset catalog.",
        )
        ingest.add_argument(
            "--endpoint",
            help="Override the Census API endpoint used for online acquisition.",
        )
        ingest.add_argument("--notes", default="")
        ingest.add_argument(
            "--source-format",
            choices=["auto", "json", "csv"],
            default="auto",
        )
        ingest.add_argument(
            "--expected-sha256",
            help="Reject acquisition unless the official source has this SHA-256.",
        )
        ingest.add_argument(
            "--api-key",
            default=os.getenv("CENSUS_API_KEY"),
            help="Census API key; defaults to CENSUS_API_KEY.",
        )

    activate = commands.add_parser("activate")
    activate.add_argument("--dataset", choices=[item.value for item in DatasetKind], required=True)
    activate.add_argument("--version", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--dataset", choices=[item.value for item in DatasetKind], required=True)
    validate.add_argument("--version", required=True)

    rollback = commands.add_parser("rollback")
    rollback.add_argument("--dataset", choices=[item.value for item in DatasetKind], required=True)
    commands.add_parser("status")
    return parser


def main() -> None:
    args = _parser().parse_args()
    store = PublicDataStore(args.store)
    if args.command in {"stage", "refresh"}:
        catalog = load_dataset_catalog(args.catalog)
        dataset = DatasetKind(args.dataset)
        definition = catalog.definition(dataset)
        if definition is None:
            raise SystemExit(f"{dataset.value} is not present in {args.catalog}.")
        release = DatasetRelease(
            dataset=dataset,
            version=args.version,
            data_year=args.data_year,
            release_date=args.release_date,
            source_url=args.source_url or definition.source_url,
            source_name=definition.source_name,
            license=definition.license,
            geographic_granularity=definition.geographic_granularity,
            refresh_cadence=definition.refresh_cadence,
            adapter="offline_fixture",
            notes=args.notes,
        )
        adapter = _adapter(
            release,
            normalized_source=args.source,
            official_source=args.official_source,
            api_key=args.api_key,
            expected_sha256=args.expected_sha256,
            source_format=args.source_format,
            endpoint=args.endpoint,
        )
        staged = store.stage(adapter)
        result: dict[str, Any] = {
            "operation": "stage",
            "dataset": staged.dataset.value,
            "version": staged.version,
            "record_count": staged.manifest.record_count,
            "content_sha256": staged.manifest.content_sha256,
            "path": str(staged.path),
        }
        if args.command == "refresh":
            active = store.activate(dataset, args.version)
            result["operation"] = "refresh"
            result["status"] = active.status.value
            result["activated_at"] = active.activated_at
        print(json.dumps(result, indent=2, default=str))
        return
    if args.command == "activate":
        manifest = store.activate(DatasetKind(args.dataset), args.version)
        print(manifest.model_dump_json(indent=2))
        return
    if args.command == "validate":
        manifest = store.validate(DatasetKind(args.dataset), args.version)
        print(manifest.model_dump_json(indent=2))
        return
    if args.command == "rollback":
        manifest = store.rollback(DatasetKind(args.dataset))
        print(manifest.model_dump_json(indent=2))
        return
    print(store.registry().model_dump_json(indent=2))


def _adapter(
    release: DatasetRelease,
    *,
    normalized_source: Path | None,
    official_source: Path | None,
    api_key: str | None,
    expected_sha256: str | None,
    source_format: str,
    endpoint: str | None,
) -> PublicDataAdapter:
    if normalized_source is not None:
        return OfflineFixtureAdapter(release, normalized_source)
    if release.dataset not in {DatasetKind.acs, DatasetKind.cbp, DatasetKind.nes}:
        raise SystemExit("NOAA and FEMA require a reviewed normalized --source extract.")
    transport = (
        FilePublicDataTransport(official_source, source_url=release.source_url)
        if official_source is not None
        else None
    )
    if release.dataset is DatasetKind.acs:
        return ACSAdapter(
            release,
            transport=transport,
            endpoint=endpoint,
            api_key=api_key,
            expected_sha256=expected_sha256,
            source_format=source_format,
        )
    if release.dataset is DatasetKind.cbp:
        return CBPAdapter(
            release,
            transport=transport,
            endpoint=endpoint,
            api_key=api_key,
            expected_sha256=expected_sha256,
            source_format=source_format,
        )
    return NESAdapter(
        release,
        transport=transport,
        endpoint=endpoint,
        api_key=api_key,
        expected_sha256=expected_sha256,
        source_format=source_format,
    )


if __name__ == "__main__":
    main()
