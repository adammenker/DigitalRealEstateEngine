#!/usr/bin/env sh
set -eu

environment="${1:-}"
release_sha="${2:-}"
case "$environment" in
  staging|production) ;;
  *) echo "environment must be staging or production" >&2; exit 2 ;;
esac
test -n "$release_sha" || { echo "release SHA is required" >&2; exit 2; }
test -f "release/${release_sha}.json" || {
  echo "release manifest not found" >&2
  exit 2
}
python3 scripts/release_manifest.py \
  --verify "release/${release_sha}.json" \
  --expected-environment "$environment" \
  --expected-sha "$release_sha"

: "${ROLLBACK_COMMAND:?Configure in protected CI environment}"
export ROLLBACK_RELEASE_MANIFEST="release/${release_sha}.json"
sh -c "$ROLLBACK_COMMAND"
