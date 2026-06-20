#!/usr/bin/env bash
# Pre-build: clones Fluent GTK theme, compiles the Dark gnome-shell CSS
# and copies SVG assets.  The GDM engine reads these at runtime instead
# of depending on anduinos-fluent-gtk-theme.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git
need_cmd sassc

FLUENT_GTK_COMMIT="9fc5291"
OUT_DIR="$SCRIPT_DIR/deploy/fluent-gnome-shell"

rm -rf "$OUT_DIR" /tmp/Fluent-gtk-theme-gdm
git clone https://gitlab.aiursoft.com/mirror/fluent-gtk-theme/ /tmp/Fluent-gtk-theme-gdm
git -C /tmp/Fluent-gtk-theme-gdm checkout "$FLUENT_GTK_COMMIT"

echo "Building Fluent gnome-shell CSS (dark, noborder, round)..."
# Build to a temp directory, then extract just the gnome-shell portion.
BUILD_TMP="$(mktemp -d)"
(
    cd /tmp/Fluent-gtk-theme-gdm
    bash ./install.sh --tweaks noborder round --theme default --color dark -d "$BUILD_TMP"
)
# install.sh lays out <theme-name>/gnome-shell/gnome-shell.css and assets/
THEME_DIR="$BUILD_TMP/Fluent-round-Dark/gnome-shell"
if [[ -d "$THEME_DIR" ]]; then
    mkdir -p "$(dirname "$OUT_DIR")"
    mv "$THEME_DIR" "$OUT_DIR"
else
    echo "ERROR: install.sh did not produce gnome-shell CSS" >&2
    exit 1
fi
rm -rf "$BUILD_TMP"

echo "Verifying..."
[ -f "$OUT_DIR/gnome-shell.css" ] || { echo "ERROR: gnome-shell.css not built" >&2; exit 1; }
[ -f "$OUT_DIR/assets/checkbox.svg" ] || { echo "ERROR: assets missing" >&2; exit 1; }

rm -rf /tmp/Fluent-gtk-theme-gdm
echo "Done. Fluent gnome-shell CSS + assets → $OUT_DIR"
