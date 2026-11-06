#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f certs/generated/mds-1/server.crt ]; then
  python scripts/generate_certs.py
fi
docker compose up --build -d
docker compose ps

