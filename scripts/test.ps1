$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m pytest -q -s --junitxml=evidence\pytest.xml

