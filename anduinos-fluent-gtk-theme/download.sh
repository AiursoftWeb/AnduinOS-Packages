#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git
need_cmd sassc

FLUENT_GTK_COMMIT="9fc5291"

rm -rf "$SCRIPT_DIR/deploy" /tmp/Fluent-gtk-theme
git clone https://gitlab.aiursoft.com/mirror/fluent-gtk-theme/ /tmp/Fluent-gtk-theme
git -C /tmp/Fluent-gtk-theme checkout "$FLUENT_GTK_COMMIT"

echo "Building Fluent GTK theme (all colors, noborder + round)..."
mkdir -p "$SCRIPT_DIR/deploy/themes"
(
    cd /tmp/Fluent-gtk-theme
    # sassc is required at build time; GS_VERSION defaults to 48-0 when
    # gnome-shell is not installed on the build server.
    ./install.sh --tweaks noborder round --theme all -d "$SCRIPT_DIR/deploy/themes"
)

# Verify sassc actually produced output — install.sh has no set -e and
# may silently continue if sassc crashes or is missing.
echo "Verifying built CSS files..."
missing=0
for theme_dir in "$SCRIPT_DIR"/deploy/themes/*/; do
    theme_name="$(basename "$theme_dir")"
    for css in gtk-3.0/gtk.css gtk-3.0/gtk-dark.css gtk-4.0/gtk.css gtk-4.0/gtk-dark.css gnome-shell/gnome-shell.css; do
        if [ ! -f "$theme_dir/$css" ]; then
            echo "ERROR: $css missing in $theme_name" >&2
            missing=$((missing + 1))
        fi
    done
done
if [ "$missing" -gt 0 ]; then
    echo "ERROR: $missing CSS file(s) missing — build is incomplete." >&2
    exit 1
fi

rm -rf /tmp/Fluent-gtk-theme
echo "Done. Pre-built GTK themes are in deploy/themes/."
