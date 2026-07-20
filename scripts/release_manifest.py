from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory


def _config_version(path: Path) -> str:
    payload = yaml.safe_load(path.read_text()) or {}
    return str(
        payload.get("version")
        or payload.get("config_version")
        or payload.get("assessment_version")
        or "unversioned"
    )


def _prefilter_version(root: Path) -> str:
    pointer = yaml.safe_load((root / "config/market_prefilter.yaml").read_text()) or {}
    target = pointer.get("addressable_market_config")
    if isinstance(target, str):
        return _config_version(root / "config" / target)
    return _config_version(root / "config/market_prefilter.yaml")


def _geography_version(root: Path, environment: dict[str, str]) -> str:
    configured = environment.get("GEOGRAPHY_VERSION")
    if configured:
        return configured
    database = root / "data/us_geography.sqlite3"
    if not database.is_file():
        return "unavailable"
    import sqlite3

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'dataset_version'"
        ).fetchone()
    return str(row[0]) if row else "unversioned"


def build_manifest(root: Path, environment: dict[str, str]) -> dict[str, Any]:
    alembic = Config(str(root / "alembic.ini"))
    alembic.set_main_option("script_location", str(root / "migrations"))
    heads = ScriptDirectory.from_config(alembic).get_heads()
    if len(heads) != 1:
        raise ValueError(f"Release requires one migration head; found {heads}.")
    manifest: dict[str, Any] = {
        "environment": environment["ENVIRONMENT"],
        "git_sha": environment["GIT_SHA"],
        "api_image_digest": environment["API_DIGEST"],
        "worker_image_digest": environment["API_DIGEST"],
        "frontend_image_digest": environment["FRONTEND_DIGEST"],
        "migration_version": heads[0],
        "scoring_version": _config_version(root / "config/scoring.yaml"),
        "evidence_quality_version": _config_version(root / "config/evidence_quality.yaml"),
        "service_catalog_version": _config_version(root / "config/services.yaml"),
        "geography_version": _geography_version(root, environment),
        "prefilter_version": _prefilter_version(root),
        "release_notes": environment["RELEASE_NOTES"],
    }
    manifest["release_fingerprint"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(Path(__file__).resolve().parents[1], dict(os.environ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
