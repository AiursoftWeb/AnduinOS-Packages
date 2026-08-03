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
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
bash -n build.sh scripts/prebuild-check.sh scripts/postinst.sh scripts/prerm.sh scripts/postrm.sh \
    data/initramfs-hook data/initramfs-local-premount \
    data/grub.d/09_anduinos_timeback tests/vm/common.sh tests/vm/smoke.sh \
    tests/vm/rollback-cycle.sh tests/vm/rollback-resume.sh

python3 -m json.tool docs/deployment-v1.schema.json >/dev/null
python3 -m json.tool docs/rollback-v1.schema.json >/dev/null
python3 -m json.tool docs/package-transaction-v1.schema.json >/dev/null
python3 -m json.tool docs/home-snapshot-v1.schema.json >/dev/null
python3 -m json.tool docs/automatic-configuration-v1.schema.json >/dev/null
python3 - <<'PY'
import ast
from configparser import ConfigParser
from pathlib import Path
from subprocess import check_output
from xml.etree import ElementTree

apt_config = check_output(
    ["apt-config", "-c", "data/85anduinos-timeback", "dump"],
    text=True,
)
assert "/usr/libexec/anduinos-timeback-apt-hook pre" in apt_config
assert "/usr/libexec/anduinos-timeback-apt-hook post" in apt_config

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
assert systemd_service["Service"]["User"] == "root"
assert systemd_service["Service"]["NoNewPrivileges"] == "yes"
assert systemd_service["Service"]["ProtectSystem"] == "strict"
assert systemd_service["Service"]["CapabilityBoundingSet"] == "CAP_SYS_ADMIN"
assert systemd_service["Service"]["ReadWritePaths"] == "-/.snapshots -/boot"

maintenance_service = ConfigParser(strict=False)
maintenance_service.optionxform = str
maintenance_service.read("data/anduinos-timeback-maintenance.service")
assert maintenance_service["Service"]["Type"] == "oneshot"
assert maintenance_service["Service"]["ExecStart"] == "/usr/libexec/anduinos-timeback-maintenance"
assert maintenance_service["Service"]["User"] == "root"
assert maintenance_service["Service"]["NoNewPrivileges"] == "yes"
assert maintenance_service["Service"]["ProtectSystem"] == "strict"
assert maintenance_service["Service"]["CapabilityBoundingSet"] == "CAP_SYS_ADMIN"
assert maintenance_service["Service"]["ReadWritePaths"] == "-/.snapshots"

maintenance_timer = ConfigParser(strict=False)
maintenance_timer.optionxform = str
maintenance_timer.read("data/anduinos-timeback-maintenance.timer")
assert maintenance_timer["Timer"]["Persistent"] == "true"
assert maintenance_timer["Timer"]["Unit"] == "anduinos-timeback-maintenance.service"
assert maintenance_timer["Install"]["WantedBy"] == "timers.target"

vm_resume = ConfigParser(strict=False)
vm_resume.optionxform = str
vm_resume.read("tests/vm/anduinos-timeback-vm-resume.service")
assert vm_resume["Service"]["Type"] == "oneshot"
assert vm_resume["Service"]["ExecStart"] == "/usr/local/libexec/anduinos-timeback-vm-resume"
assert vm_resume["Unit"]["After"] == "anduinos-timeback-confirm.service"

vm_scripts = [
    Path("tests/vm/common.sh"),
    Path("tests/vm/smoke.sh"),
    Path("tests/vm/rollback-cycle.sh"),
    Path("tests/vm/rollback-resume.sh"),
]
assert all(path.stat().st_mode & 0o111 for path in vm_scripts)
combined = "\n".join(path.read_text() for path in vm_scripts)
assert "DESTROY_THIS_DISPOSABLE_VM" in combined
assert "anduinos.timeback.test=1" in combined
assert "systemd-detect-virt --vm" in combined
assert "rm -rf" not in combined

powercut_path = Path("tests/vm/powercut.py")
assert powercut_path.stat().st_mode & 0o111
ast.parse(powercut_path.read_text(), filename=str(powercut_path))
powercut = powercut_path.read_text()
for checkpoint in (
    "apply-started",
    "writable-target-created",
    "current-root-protected",
    "target-root-activated",
    "booted-unconfirmed-recorded",
    "revert-started",
    "restored-root-moved-aside",
    "fallback-root-activated",
    "discarded-root-deleted",
    "reverted-recorded",
):
    assert checkpoint in powercut
assert "DESTROY_TM5_QEMU_OVERLAYS" in powercut
assert "SIGKILL" in powercut
assert "shell=True" not in powercut

confirm_service = ConfigParser(strict=False)
confirm_service.optionxform = str
confirm_service.read("data/anduinos-timeback-confirm.service")
assert confirm_service["Service"]["Type"] == "oneshot"
assert confirm_service["Service"]["ExecStart"] == "/usr/libexec/anduinos-timeback-confirm"
assert confirm_service["Service"]["PrivateMounts"] == "yes"
assert confirm_service["Service"]["CapabilityBoundingSet"] == "CAP_SYS_ADMIN"
assert confirm_service["Install"]["WantedBy"] == "multi-user.target"

interface = ElementTree.parse(
    "data/com.anduinos.timebackmachine.xml"
).getroot().find("interface")
assert interface is not None
methods = {method.attrib["name"] for method in interface.findall("method")}
assert {
    "InspectLayout",
    "ListDeployments",
    "VerifyRecoveryPoint",
    "CreateRecoveryPoint",
    "SetPinned",
    "DeleteRecoveryPoint",
    "ScheduleRollback",
    "CancelPendingRollback",
    "InspectRetention",
    "RunRetention",
}.issubset(methods)
PY

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate data/com.anduinos.timebackmachine.desktop
fi
