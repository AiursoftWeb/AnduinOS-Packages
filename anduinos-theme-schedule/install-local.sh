#!/bin/bash
# User-session install of the Dark Style GNOME Shell extension.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$HOME/.local/share/glib-2.0/schemas"
install -m 644 "$ROOT/data/com.anduinos.ThemeSchedule.gschema.xml" \
    "$HOME/.local/share/glib-2.0/schemas/com.anduinos.ThemeSchedule.gschema.xml"
glib-compile-schemas "$HOME/.local/share/glib-2.0/schemas"

mkdir -p "$HOME/.local/share/gnome-shell/extensions"
rm -rf "$HOME/.local/share/gnome-shell/extensions/dark-style@anduinos.com"
cp -a "$ROOT/extension/dark-style@anduinos.com" \
    "$HOME/.local/share/gnome-shell/extensions/dark-style@anduinos.com"

systemctl --user disable --now anduinos-theme-schedule.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/anduinos-theme-schedule.service"
rm -f "$HOME/.local/bin/anduinos-theme-schedule"
systemctl --user daemon-reload 2>/dev/null || true

python3 - <<'PY'
import ast, subprocess
raw = subprocess.check_output(['gsettings', 'get', 'org.gnome.shell', 'enabled-extensions'], text=True)
items = ast.literal_eval(raw.strip())
uuid = 'dark-style@anduinos.com'
if uuid not in items:
    items.append(uuid)
    value = '[' + ', '.join(f"'{item}'" for item in items) + ']'
    subprocess.check_call(['gsettings', 'set', 'org.gnome.shell', 'enabled-extensions', value])
PY

gnome-extensions enable dark-style@anduinos.com 2>/dev/null || true

echo "Installed dark-style@anduinos.com"
echo "Open Quick Settings → Dark Style → Sunset to Sunrise"
echo "On Wayland, log out and back in if the tile has not changed yet."
