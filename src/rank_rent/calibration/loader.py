from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from rank_rent.calibration.models import (
    BenchmarkManifest,
    ClassificationLibrary,
    ProviderLibrary,
    ScenarioLibrary,
)

DEFAULT_MANIFEST = Path("config/benchmarks/manifest.yaml")


class CalibrationConfigError(ValueError):
    pass


def project_path(project_root: Path, configured_path: str | Path) -> Path:
    path = Path(configured_path)
    return path if path.is_absolute() else project_root / path


def load_yaml_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CalibrationConfigError(f"Unable to load {path}: {exc}") from exc
    try:
        return model.model_validate(payload)
    except ValueError as exc:
        raise CalibrationConfigError(f"Invalid benchmark configuration {path}: {exc}") from exc


def load_manifest(
    project_root: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> BenchmarkManifest:
    return load_yaml_model(project_path(project_root, manifest_path), BenchmarkManifest)


def load_libraries(
    project_root: Path,
    manifest: BenchmarkManifest,
) -> tuple[ScenarioLibrary, ClassificationLibrary, ProviderLibrary]:
    return (
        load_yaml_model(
            project_path(project_root, manifest.scenario_library),
            ScenarioLibrary,
        ),
        load_yaml_model(
            project_path(project_root, manifest.classification_library),
            ClassificationLibrary,
        ),
        load_yaml_model(
            project_path(project_root, manifest.provider_library),
            ProviderLibrary,
        ),
    )


def benchmark_config_hash(
    project_root: Path,
    manifest: BenchmarkManifest,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> str:
    paths = [
        project_path(project_root, manifest_path),
        project_path(project_root, manifest.active_scoring_config),
        project_path(project_root, manifest.scenario_library),
        project_path(project_root, manifest.classification_library),
        project_path(project_root, manifest.provider_library),
        project_path(project_root, manifest.service_catalog),
        project_path(project_root, manifest.evidence_quality_config),
        project_path(project_root, manifest.serp_classification_config),
        *[
            project_path(project_root, config_path)
            for config_path in manifest.scoring_configs.values()
        ],
    ]
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        relative = path.relative_to(project_root) if path.is_relative_to(project_root) else path
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_raw_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CalibrationConfigError(f"Unable to load {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CalibrationConfigError(f"{path} must contain a YAML mapping")
    return payload
