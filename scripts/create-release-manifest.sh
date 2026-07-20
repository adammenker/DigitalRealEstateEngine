#!/usr/bin/env sh
set -eu

: "${ENVIRONMENT:?}"
: "${GIT_SHA:?}"
: "${API_DIGEST:?}"
: "${FRONTEND_DIGEST:?}"
: "${RELEASE_NOTES:?}"

mkdir -p release
python3 scripts/release_manifest.py --output "release/${GIT_SHA}.json"
