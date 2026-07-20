from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


class SecretResolutionError(ValueError):
    pass


def is_secret_reference(value: str) -> bool:
    return value.startswith(("env://", "file://", "aws-secretsmanager://", "vault://"))


def resolve_secret_reference(value: str, *, required: bool = False) -> str:
    """Resolve local secret refs; managed-store refs are injected by the deployment platform."""
    if not value:
        if required:
            raise SecretResolutionError("Required secret reference is empty.")
        return ""
    if value.startswith("env://"):
        name = value.removeprefix("env://")
        if not name or not name.replace("_", "").isalnum():
            raise SecretResolutionError("Invalid environment secret reference.")
        result = os.environ.get(name, "")
    elif value.startswith("file://"):
        parsed = urlparse(value)
        path = Path(parsed.path)
        if not path.is_absolute() or not path.is_relative_to("/run/secrets"):
            raise SecretResolutionError("Secret files must be below /run/secrets.")
        try:
            result = path.read_text().strip()
        except OSError as exc:
            raise SecretResolutionError("Secret file is unavailable.") from exc
    elif value.startswith(("aws-secretsmanager://", "vault://")):
        raise SecretResolutionError(
            "Managed secret references must be resolved and injected by the runtime platform."
        )
    else:
        result = value
    if required and not result:
        raise SecretResolutionError("Required secret resolved to an empty value.")
    return result

