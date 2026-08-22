#!/bin/bash
# User-session install of the sunrise/sunset Dark Style scheduler.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="$ROOT/obj/anduinos-theme-schedule"
if [ ! -x "$BIN" ]; then
    bash "$ROOT/build.sh"
    BIN="$ROOT/obj/anduinos-theme-schedule"
fi

install -m 755 "$BIN" "$HOME/.local/bin/anduinos-theme-schedule"

mkdir -p "$HOME/.local/share/glib-2.0/schemas"
install -m 644 "$ROOT/data/com.anduinos.ThemeSchedule.gschema.xml" \
    "$HOME/.local/share/glib-2.0/schemas/com.anduinos.ThemeSchedule.gschema.xml"
glib-compile-schemas "$HOME/.local/share/glib-2.0/schemas"

mkdir -p "$HOME/.local/share/gnome-shell/extensions"
rm -rf "$HOME/.local/share/gnome-shell/extensions/dark-style@anduinos.com"
cp -a "$ROOT/extension/dark-style@anduinos.com" \
    "$HOME/.local/share/gnome-shell/extensions/dark-style@anduinos.com"

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/anduinos-theme-schedule.service" <<EOF
[Unit]
Description=AnduinOS Dark Style sunrise/sunset scheduler
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
Environment=GSETTINGS_SCHEMA_DIR=$HOME/.local/share/glib-2.0/schemas
ExecStart=$HOME/.local/bin/anduinos-theme-schedule
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now anduinos-theme-schedule.service

gnome-extensions enable dark-style@anduinos.com 2>/dev/null || true

echo "Installed $HOME/.local/bin/anduinos-theme-schedule"
echo "Extension: dark-style@anduinos.com"
echo "Open Quick Settings → Dark Style → Sunset to Sunrise"
echo "If the tile has not changed yet, log out and back in (Wayland cannot restart GNOME Shell in-place)."
