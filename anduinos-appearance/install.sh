#!/bin/bash
# Install the Rust AnduinOS Appearance binary over the Python GUI.
# The Python anduinos_appearance library is left in place for OOBE.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="$ROOT/obj/anduinos-appearance"

if [ ! -x "$BIN" ]; then
    echo "Missing $BIN — build first with: bash build.sh" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

install -m 755 "$BIN" /usr/bin/anduinos-appearance
install -m 644 "$ROOT/data/com.anduinos.Appearance.service" \
    /usr/share/dbus-1/services/com.anduinos.Appearance.service

desktop=/usr/share/applications/anduinos-appearance.desktop
if [ -f "$desktop" ] && ! grep -q '^DBusActivatable=true' "$desktop"; then
    sed -i '/^Terminal=false/a DBusActivatable=true' "$desktop"
fi

update-desktop-database /usr/share/applications 2>/dev/null || true

echo "Installed $(file -b /usr/bin/anduinos-appearance)"
echo "D-Bus service: /usr/share/dbus-1/services/com.anduinos.Appearance.service"
