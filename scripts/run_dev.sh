#!/usr/bin/env bash
# Spuštění ze zdrojů (Linux/macOS):  ./scripts/run_dev.sh [--diagnose|--test]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=$(command -v python3.13 || command -v python3.12 || command -v python3)
[ -d .venv ] || { "$PY" -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt; }
export PYTHONPATH="$PWD/src"
case "${1:-}" in
  --test) exec .venv/bin/python -m pytest -q ;;
  --diagnose) exec .venv/bin/python -m netmonitor --diagnose ;;
  *) exec .venv/bin/python -m netmonitor --debug ;;
esac
