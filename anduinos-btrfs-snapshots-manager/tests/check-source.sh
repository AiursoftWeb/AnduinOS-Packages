#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

template="$ROOT/po/anduinos-btrfs-snapshots-manager.pot"
for catalog in "$ROOT"/po/*.po; do
    msgcmp --no-fuzzy-matching "$catalog" "$template" >/dev/null
done

factory_messages=(
    "Delete Factory Recovery Point?"
    "Delete and Disable Factory Recovery"
    "Deleting factory recovery point…"
    "Checking factory reset availability…"
    "Factory Reset Is Not Available"
    "This system does not support factory reset. Reinstall AnduinOS and choose the Btrfs filesystem to enable it."
    "Factory Reset Is Not Ready"
    "Reset AnduinOS to Its Initial State?"
    "Factory reset will restore system files, installed packages, and system settings to the original New OS state. Personal files in Home will not change. A safety snapshot of the current system will be created first. Recovery will then be armed and this computer will restart automatically within 60 seconds."
    "Return to the initial New OS state"
    "Saved as a safety snapshot before reset"
    "Reset and Restart"
    "“New OS” is the original system state created during installation. Deleting it will disable the ability to reset AnduinOS to its initial state. This cannot be undone without reinstalling the operating system. Your personal files will not be deleted by this action."
    "Preparing factory reset…"
    "Restore the system to New OS. Personal files are kept by default."
    "Roll back user data"
    "Restore all users’ files and settings to their initial state. Snapshot history is kept."
    "Unavailable because the factory Home recovery point is missing or damaged."
    "Safety snapshots are created before rollback. Restart follows within 60 seconds."
    "Deleting New OS disables factory recovery for this snapshot category. Current files and other snapshots are kept."
    "All users’ files and settings"
    "A safety snapshot is created first. Snapshot history is kept."
    "Restart Required — Rollback Armed"
    "Rollback is ready. Save your work; this computer will restart within 60 seconds."
)
localization_dir="$(mktemp -d)"
trap 'rm -rf "$localization_dir"' EXIT
for catalog in "$ROOT"/po/*.po; do
    locale="$(basename "$catalog" .po)"
    [[ "$locale" == en_* ]] && continue
    compiled="$localization_dir/$locale.mo"
    msgfmt --check --check-format "$catalog" -o "$compiled"
    python3 - "$catalog" "$compiled" "${factory_messages[@]}" <<'PY'
import gettext
import sys

catalog, compiled, *messages = sys.argv[1:]
with open(compiled, "rb") as stream:
    translations = gettext.GNUTranslations(stream)
for message in messages:
    if translations.gettext(message) == message:
        raise SystemExit(f"{catalog}: factory-reset message is not localized: {message}")
PY
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
