from __future__ import annotations

import importlib.metadata
import json
import re
from pathlib import Path

DENIED_LICENSE_PATTERNS = (
    re.compile(r"\bAGPL(?:V|-| )?3"),
    re.compile(r"(?<!L)\bGPL(?:V|-| )?3"),
    re.compile(r"\bSSPL\b"),
    re.compile(r"\bBUSL\b"),
)


def _is_denied(license_value: str) -> bool:
    normalized = license_value.upper()
    return any(pattern.search(normalized) for pattern in DENIED_LICENSE_PATTERNS)


def denied_licenses(project_root: Path) -> list[str]:
    violations: list[str] = []
    requirement_names = {
        match.group(1).lower().replace("-", "_")
        for line in (project_root / "requirements.lock").read_text().splitlines()
        if (match := re.match(r"^([A-Za-z0-9_.-]+)==", line))
    }
    for distribution in importlib.metadata.distributions():
        normalized = distribution.metadata["Name"].lower().replace("-", "_")
        if normalized not in requirement_names:
            continue
        license_value = " ".join(
            filter(
                None,
                [
                    distribution.metadata.get("License-Expression", ""),
                    distribution.metadata.get("License", ""),
                    *distribution.metadata.get_all("Classifier", []),
                ],
            )
        ).upper()
        if _is_denied(license_value):
            violations.append(f"python:{distribution.metadata['Name']}:{license_value}")

    package_lock = json.loads(
        (project_root / "frontend/package-lock.json").read_text()
    )
    for package_path, metadata in package_lock.get("packages", {}).items():
        if not package_path or not isinstance(metadata, dict):
            continue
        license_value = str(metadata.get("license") or "").upper()
        if _is_denied(license_value):
            violations.append(
                f"npm:{package_path.removeprefix('node_modules/')}:{license_value}"
            )
    return sorted(violations)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    violations = denied_licenses(root)
    if violations:
        raise SystemExit("Denied dependency licenses:\n" + "\n".join(violations))
    print("Dependency license policy passed.")


if __name__ == "__main__":
    main()
