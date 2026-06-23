#!/usr/bin/env bash
# Pre-build: clones AnduinOS fork of blur-my-shell for each GNOME target.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git

UUID="blur-my-shell@aunetx"
REPO="https://github.com/Anduin2017/blur-my-shell.git"
PATCH_TARGETS="${!GNOME_TARGETS[*]}"

source "$SCRIPT_DIR/../lib/gnome-versions.sh"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR" /tmp/blur-my-shell
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Cloning AnduinOS blur-my-shell fork..."
    git clone --depth 1 "$REPO" /tmp/blur-my-shell

    # Copy the extension source into the deploy directory
    cp -a /tmp/blur-my-shell/. "$DEPLOY_DIR/"
    rm -rf /tmp/blur-my-shell

    # Flatten the src/ directory: GNOME Shell expects extension.js at the root,
    # not buried in a src/ subdirectory. The gnome-extensions pack command
    # normally does this, but we deploy directly without building a zip.
    shopt -s dotglob
    cp -a "$DEPLOY_DIR/src/"* "$DEPLOY_DIR/"
    shopt -u dotglob
    rm -rf "$DEPLOY_DIR/src"

    # Flatten resources/ — icons and ui must be at the extension root
    cp -a "$DEPLOY_DIR/resources/icons" "$DEPLOY_DIR/"
    cp -a "$DEPLOY_DIR/resources/ui" "$DEPLOY_DIR/"
    rm -rf "$DEPLOY_DIR/resources"
    rm -rf "$DEPLOY_DIR/.git" "$DEPLOY_DIR/.github"
    rm -f "$DEPLOY_DIR/Makefile" "$DEPLOY_DIR/README.md" "$DEPLOY_DIR/.gitignore"

    # Patch metadata.json to claim support for the target GNOME version
    echo "[$SUITE] Patching metadata.json for GNOME $TARGET..."
    python3 -c "
import json
with open('$DEPLOY_DIR/metadata.json') as f:
    meta = json.load(f)
existing = meta.get('shell-version', [])
if '$TARGET' not in existing:
    meta['shell-version'] = existing + ['$TARGET']
with open('$DEPLOY_DIR/metadata.json', 'w') as f:
    json.dump(meta, f, indent=2)
"
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}blur-my-shell@aunetx/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
