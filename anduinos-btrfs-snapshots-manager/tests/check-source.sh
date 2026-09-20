#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

template="$ROOT/po/anduinos-btrfs-snapshots-manager.pot"
for catalog in "$ROOT"/po/*.po; do
    msgcmp --no-fuzzy-matching "$catalog" "$template" >/dev/null
done

python3 - "$ROOT/scripts/screenshot-demo-service.py" \
    "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py" <<'PY'
import sys

for name in sys.argv[1:]:
    with open(name, encoding="utf-8") as source:
        compile(source.read(), name, "exec")
PY

python3 "$ROOT/scripts/check-i18n.py"
bash "$ROOT/tests/test-initramfs-integration.sh"
