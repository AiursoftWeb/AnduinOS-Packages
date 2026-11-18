#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="blur-my-shell@aunetx"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # Migration presets must come from the schema, not overridden dconf defaults.
    python3 - "$DEPLOY_DIR/settings/settings_updater.js" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
old = "gsettings.get_default_value('pipelines')"
new = "gsettings.settings_schema.get_key('pipelines').get_default_value()"
if source.count(old) == 1:
    path.write_text(source.replace(old, new))
elif old in source or new not in source:
    raise SystemExit("Unsupported Blur My Shell preset migration; inspect upstream changes")
PY

    # The local panel geometry fix was merged upstream in PR #907.
    # Keep the resolver-selected upstream implementation, including later fixes.
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}blur-my-shell@aunetx/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
