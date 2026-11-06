#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python -m pytest -q -s --junitxml=evidence/pytest.xml

