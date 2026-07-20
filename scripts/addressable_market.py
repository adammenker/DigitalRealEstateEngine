#!/usr/bin/env python3
"""Run zero-cost addressable-market batches without the application API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rank_rent.services.market_prefilter import AddressableMarketPrefilter
from rank_rent.services.service_catalog import load_service_catalog
from rank_rent.services.us_geography import USGeographyIndex

ROOT = Path(__file__).parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    top = commands.add_parser("top")
    top.add_argument("--service", required=True)
    top.add_argument("--state", action="append", default=[])
    top.add_argument("--limit", type=int, default=100)
    top.add_argument("--minimum-population", type=int)

    batch = commands.add_parser("batch")
    batch.add_argument("--service", required=True)
    batch.add_argument(
        "--markets",
        type=Path,
        required=True,
        help="JSON list or newline-delimited canonical geography IDs.",
    )
    batch.add_argument("--limit", type=int)
    return parser


def _market_ids(path: Path) -> list[str]:
    text = path.read_text()
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if not isinstance(payload, list) or not all(
            isinstance(item, str) for item in payload
        ):
            raise ValueError("Markets JSON must be a list of geography ID strings.")
        return payload
    return [line.strip() for line in text.splitlines() if line.strip()]


def main() -> None:
    args = _parser().parse_args()
    catalog = load_service_catalog(ROOT / "config" / "services.yaml")
    resolution = catalog.resolve(args.service)
    if resolution is None or not resolution.service.enabled:
        raise SystemExit(f"Unknown or disabled configured service: {args.service}")
    prefilter = AddressableMarketPrefilter(
        USGeographyIndex(ROOT / "data" / "us_geography.sqlite3"),
        ROOT / "config" / "market_prefilter.yaml",
    )
    if args.command == "top":
        assessments, candidate_count = prefilter.rank_markets(
            resolution.service,
            states=args.state,
            limit=args.limit,
            minimum_population=args.minimum_population,
        )
        payload = {
            "assessment_version": prefilter.config.assessment_version,
            "service_family_id": resolution.service.id,
            "candidate_count": candidate_count,
            "returned_count": len(assessments),
            "zero_cost": True,
            "paid_api_calls": 0,
            "assessments": [
                assessment.model_dump(mode="json") for assessment in assessments
            ],
        }
    else:
        batch = prefilter.assess_geography_ids(
            resolution.service,
            _market_ids(args.markets),
            limit=args.limit,
        )
        payload = batch.model_dump(mode="json")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
