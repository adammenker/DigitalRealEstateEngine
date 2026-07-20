#!/usr/bin/env sh
set -eu

action="${1:-}"
environment="${2:-}"
case "$environment" in
  staging|production) ;;
  *) echo "environment must be staging or production" >&2; exit 2 ;;
esac

# This reference command deliberately delegates provider-specific identifiers to
# protected CI environment variables and never prints their values.
case "$action" in
  backup-check)
    : "${BACKUP_VERIFICATION_COMMAND:?Configure in protected CI environment}"
    sh -c "$BACKUP_VERIFICATION_COMMAND"
    ;;
  migrate)
    : "${MIGRATION_TASK_COMMAND:?Configure in protected CI environment}"
    sh -c "$MIGRATION_TASK_COMMAND"
    ;;
  publish-manifest)
    : "${RELEASE_MANIFEST_PUBLISH_COMMAND:?Configure in protected CI environment}"
    sh -c "$RELEASE_MANIFEST_PUBLISH_COMMAND"
    ;;
  backend)
    : "${BACKEND_DEPLOY_COMMAND:?Configure in protected CI environment}"
    sh -c "$BACKEND_DEPLOY_COMMAND"
    ;;
  frontend)
    : "${FRONTEND_DEPLOY_COMMAND:?Configure in protected CI environment}"
    sh -c "$FRONTEND_DEPLOY_COMMAND"
    ;;
  smoke)
    : "${SMOKE_TEST_URL:?Configure in protected CI environment}"
    python - "$SMOKE_TEST_URL" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1].rstrip("/")
for path in ("/live", "/ready", "/health/dependencies"):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        payload = json.load(response)
        if response.status != 200 or payload["status"] != "ok":
            raise SystemExit(f"smoke check failed: {path}")
PY
    ;;
  *) echo "unknown deployment action" >&2; exit 2 ;;
esac
