from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from alembic.runtime.migration import MigrationContext
from sqlalchemy.orm import Session

from rank_rent.services.us_geography import USGeographyError, USGeographyIndex
from rank_rent.settings import Settings


def _versioned_config(path: Path) -> str:
    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return "unavailable"
    return str(
        payload.get("version")
        or payload.get("config_version")
        or payload.get("assessment_version")
        or "unversioned"
    )


def _prefilter_version(root: Path) -> str:
    pointer_path = root / "config/market_prefilter.yaml"
    try:
        pointer = yaml.safe_load(pointer_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return "unavailable"
    target = pointer.get("addressable_market_config")
    if not isinstance(target, str):
        return _versioned_config(pointer_path)
    return _versioned_config(root / "config" / target)


def _checksum(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unavailable"


def _geography_version(settings: Settings) -> str:
    if settings.geography_dataset_version != "bundled":
        return settings.geography_dataset_version
    try:
        return USGeographyIndex.from_settings(settings).metadata().get(
            "dataset_version", "unversioned"
        )
    except (OSError, USGeographyError):
        return "unavailable"


def release_metadata(session: Session, settings: Settings) -> dict[str, Any]:
    root = settings.project_root
    migration_version = MigrationContext.configure(session.connection()).get_current_revision()
    metadata = {
        "git_sha": settings.release_git_sha,
        "image_digest": settings.release_image_digest,
        "frontend_image_digest": settings.release_frontend_image_digest,
        "migration_version": migration_version,
        "scoring_version": _versioned_config(root / "config/scoring.yaml"),
        "evidence_quality_version": _versioned_config(root / "config/evidence_quality.yaml"),
        "service_catalog_version": _versioned_config(root / "config/services.yaml"),
        "geography_version": _geography_version(settings),
        "prefilter_version": _prefilter_version(root),
        "release_notes": settings.release_notes,
        "environment": settings.app_env,
    }
    metadata["release_fingerprint"] = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, default=str).encode()
    ).hexdigest()
    metadata["service_catalog_checksum"] = _checksum(root / "config/services.yaml")
    return metadata
