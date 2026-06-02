#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version,
# then applies AnduinOS customizations.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="dash-to-panel@jderose9.github.com"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # ── AnduinOS customizations ───────────────────────────────────────────
    echo "[$SUITE] Applying AnduinOS panel layout to $DEPLOY_DIR/panelPositions.js..."

    sed -i '/export const defaults = \[/,/^\]$/c\
// AnduinOS custom default panel layout\
export const defaults = [\
  { element: LEFT_BOX, visible: true, position: STACKED_TL },\
  { element: CENTER_BOX, visible: true, position: CENTERED_MONITOR },\
  { element: TASKBAR, visible: true, position: CENTERED_MONITOR },\
  { element: RIGHT_BOX, visible: true, position: STACKED_BR },\
  { element: SYSTEM_MENU, visible: true, position: STACKED_BR },\
  { element: DATE_MENU, visible: true, position: STACKED_BR },\
  { element: DESKTOP_BTN, visible: true, position: STACKED_BR },\
];' \
        "$DEPLOY_DIR/panelPositions.js"

    # Patch Chinese locale: rename "Dash to Panel 设置" → "任务栏设置"
    zh_mo="$DEPLOY_DIR/locale/zh_CN/LC_MESSAGES/dash-to-panel.mo"
    if [[ -f "$zh_mo" ]]; then
        echo "[$SUITE] Patching zh_CN locale..."
        msgunfmt "$zh_mo" -o /tmp/dash-to-panel.po
        sed -i "s/Dash to Panel 设置/任务栏设置/g" /tmp/dash-to-panel.po
        msgfmt /tmp/dash-to-panel.po -o "$zh_mo"
        rm -f /tmp/dash-to-panel.po
    fi
done

echo "Done."
