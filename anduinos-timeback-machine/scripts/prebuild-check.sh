#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

if cargo fmt --version >/dev/null 2>&1; then
    cargo fmt --all --check
else
    echo "rustfmt is unavailable; formatting check skipped"
fi
cargo test --all-targets
bash -n build.sh scripts/prebuild-check.sh scripts/postinst.sh scripts/prerm.sh scripts/postrm.sh

python3 -m json.tool docs/deployment-v1.schema.json >/dev/null
python3 - <<'PY'
from configparser import ConfigParser
from pathlib import Path
from xml.etree import ElementTree

for path in (
    Path("data/com.anduinos.TimebackMachine1.conf"),
    Path("data/com.anduinos.timebackmachine.policy"),
    Path("data/com.anduinos.timebackmachine.xml"),
):
    ElementTree.parse(path)

dbus_service = ConfigParser()
dbus_service.optionxform = str
dbus_service.read("data/com.anduinos.TimebackMachine1.service")
assert dbus_service["D-BUS Service"]["Name"] == "com.anduinos.TimebackMachine1"
assert dbus_service["D-BUS Service"]["SystemdService"] == "anduinos-timebackd.service"

systemd_service = ConfigParser(strict=False)
systemd_service.optionxform = str
systemd_service.read("data/anduinos-timebackd.service")
assert systemd_service["Service"]["Type"] == "dbus"
assert systemd_service["Service"]["BusName"] == "com.anduinos.TimebackMachine1"
assert systemd_service["Service"]["ExecStart"] == "/usr/libexec/anduinos-timebackd"
PY

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate data/com.anduinos.timebackmachine.desktop
fi
