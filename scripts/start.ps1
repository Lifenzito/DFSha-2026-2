$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path 'certs/generated/mds-1/server.crt')) {
    python scripts/generate_certs.py
}
docker compose up --build -d
docker compose ps

