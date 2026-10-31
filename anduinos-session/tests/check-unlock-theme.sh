#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gjs -m "$ROOT/tests/unlock-theme.test.js"
python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py' -v
