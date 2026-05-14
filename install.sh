#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "--- Bio-Harness bootstrap wrapper ---"
echo "This script is a thin wrapper around scripts/bootstrap_bioharness.py."
echo "Supported install path: Python venv + Pixi + isolated tool launchers."
echo

exec python3 "$PROJECT_ROOT/scripts/bootstrap_bioharness.py" "$@"
