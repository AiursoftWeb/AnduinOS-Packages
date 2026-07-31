#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

if cargo fmt --version >/dev/null 2>&1; then
    cargo fmt --all --check
else
    echo "rustfmt is unavailable; formatting check skipped"
fi
cargo test --all-targets
bash -n build.sh scripts/prebuild-check.sh

python3 -m json.tool docs/deployment-v1.schema.json >/dev/null
python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

for path in (
    Path("data/com.anduinos.timebackmachine.policy"),
    Path("data/com.anduinos.timebackmachine.xml"),
):
    ElementTree.parse(path)
PY

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate data/com.anduinos.timebackmachine.desktop
fi
