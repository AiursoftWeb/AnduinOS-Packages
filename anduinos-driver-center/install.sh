#!/bin/bash
# Install the Rust AnduinOS Driver Center binary over the Python GUI.
# The restricted polkit helper and Secure Boot toolkit stay in place.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="$ROOT/obj/anduinos-driver-center"

if [ ! -x "$BIN" ]; then
    echo "Missing $BIN — build first with: bash build.sh" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

install -m 755 "$BIN" /usr/bin/anduinos-driver-center
install -m 644 "$ROOT/data/com.anduinos.DriverCenter.service" \
    /usr/share/dbus-1/services/com.anduinos.DriverCenter.service

desktop=/usr/share/applications/com.anduinos.DriverCenter.desktop
if [ -f "$desktop" ] && ! grep -q '^DBusActivatable=true' "$desktop"; then
    sed -i '/^Terminal=false/a DBusActivatable=true' "$desktop"
fi

update-desktop-database /usr/share/applications 2>/dev/null || true

echo "Installed $(file -b /usr/bin/anduinos-driver-center)"
echo "D-Bus service: /usr/share/dbus-1/services/com.anduinos.DriverCenter.service"
