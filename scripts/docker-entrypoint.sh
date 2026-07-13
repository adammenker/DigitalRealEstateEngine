#!/usr/bin/env sh
set -eu

mkdir -p /data /app/generated_sites

rank-rent init-db

exec "$@"

