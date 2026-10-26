"""Read-only recovery checks using disk metadata and the local APT cache."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex

from .core import PackageState, SubprocessRunner, package_candidate_version, package_state


PACKAGE = "anduinos-recovery-env"
PROBE = "/usr/libexec/anduinos-driver-center/recovery-probe"


@dataclass(frozen=True)
class RecoveryState:
    partition: bool | None
    devices: tuple[str, ...]
    package: PackageState
    candidate: str | None
    up_to_date: bool | None
    grub_entry: bool | None
    preparation: dict = field(default_factory=dict)


def grub_entry_present(path=Path("/boot/grub/grub.cfg")) -> bool | None:
    """Inspect the generated menu, never the generator or a deployment marker."""
    try:
        with path.open() as stream:
            text = stream.read(8 * 1024 * 1024 + 1)
        if len(text) > 8 * 1024 * 1024:
            return None
        for line in text.splitlines():
            if not line.lstrip().startswith("menuentry "):
                continue
            tokens = shlex.split(line, comments=True)
            if "--id=anduinos-recovery-env" in tokens:
                return True
            if any(tokens[index:index + 2] == ["--id", PACKAGE]
                   for index in range(len(tokens) - 1)):
                return True
        return False
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return None


def recovery_state(runner=None, *, binding_path=Path("/var/lib/anduinos-recovery-env/volume.json"),
                   grub_path=Path("/boot/grub/grub.cfg")) -> RecoveryState:
    runner = runner or SubprocessRunner()
    package = package_state(PACKAGE, runner)
    candidate = package_candidate_version(PACKAGE, runner)
    up_to_date = False if not package.installed else None
    if package.installed and package.version and candidate:
        comparison = runner.run(["dpkg", "--compare-versions", package.version, "ge", candidate])
        if comparison.returncode in (0, 1):
            up_to_date = comparison.returncode == 0

    partition, devices = None, ()
    try:
        bound_uuid = None
        try:
            binding = json.loads(binding_path.read_text())
            bound_uuid = binding["volume_uuid"]
            if not isinstance(bound_uuid, str) or not bound_uuid:
                raise ValueError("Invalid recovery binding")
        except FileNotFoundError:
            pass
        result = runner.run(["lsblk", "--json", "--paths", "--list", "--output",
                             "NAME,TYPE,FSTYPE,LABEL,UUID"])
        if result.returncode == 0:
            blocks = json.loads(result.stdout)["blockdevices"]
            devices = tuple(block["name"] for block in blocks
                            if block.get("type") == "part" and block.get("fstype") == "ext4"
                            and block.get("label") == "AOS_RECOVERY"
                            and (not bound_uuid or block.get("uuid") == bound_uuid))
            # Duplicate labels/UUIDs require attention rather than a green check.
            partition = len(devices) == 1
    except (OSError, ValueError, KeyError, TypeError):
        pass

    grub_entry = grub_entry_present(grub_path)
    preparation = {}
    # The same fixed read-only probe supplies the privileged partition preview
    # and interrupted-operation status. Opening this page never executes a repair.
    result = runner.run(["pkexec", "--disable-internal-agent", PROBE])
    if result.returncode == 0:
        try:
            value = json.loads(result.stdout)
            if type(value.get("grub_entry")) is bool:
                grub_entry = value["grub_entry"]
            if isinstance(value.get("preparation"), dict):
                preparation = value["preparation"]
        except (ValueError, TypeError, AttributeError):
            pass
    if preparation.get("reason") == "interrupted":
        partition = None
    return RecoveryState(partition, devices, package, candidate, up_to_date, grub_entry, preparation)


def repair_step(state: RecoveryState) -> tuple[str | None, str]:
    """Choose one next step from the same facts shown by the three checks."""
    reason = state.preparation.get("reason", "inspection-failed")
    if reason == "interrupted":
        return None, reason
    if state.partition is None:
        return "scan", "inspection-failed"
    if state.partition is False:
        if state.devices:
            return None, "existing-volume"
        if reason == "ready" and isinstance(state.preparation.get("plan"), dict):
            return "prepare", reason
        if reason not in {"no-swap", "small-swap", "multiple-swaps", "unsupported-root", "unsupported-layout",
                          "existing-volume", "bound-volume", "missing-tools", "memory", "swap-priority"}:
            return "scan", "inspection-failed"
        return None, reason
    if state.package.installed and state.up_to_date is True and state.grub_entry is True:
        return None, "healthy"
    if reason not in {"existing-volume", "bound-volume"}:
        if reason in {"unsupported-root", "missing-tools"}:
            return None, reason
        return "scan", "inspection-failed"
    if not state.package.installed:
        return "install", "package-missing"
    if state.up_to_date is False:
        return "install", "package-update"
    if state.up_to_date is None:
        return "scan", "package-unknown"
    if state.grub_entry is None:
        return "scan", "menu-unknown"
    return "boot", "menu-missing"


def require_recovery_volume(commands):
    """Validate the same system/volume contract before a fixed repair operation."""
    from .recovery_partition import BINDING, STATE, identity
    from uuid import UUID
    if (STATE / "pending.json").exists():
        raise RuntimeError(f"Partition preparation was interrupted. Inspect the saved backups in {STATE} first.")
    root = json.loads(commands.text("findmnt", "--json", "--output", "FSTYPE,FSROOT,UUID",
                                    "--target", "/"))["filesystems"][0]
    if root.get("fstype") != "btrfs" or root.get("fsroot") != "/@root":
        raise RuntimeError("Recovery requires the installed AnduinOS Btrfs @root system")
    blocks = json.loads(commands.text("lsblk", "--json", "--paths", "--list", "--output",
                                     "NAME,TYPE,FSTYPE,LABEL,UUID,PKNAME"))["blockdevices"]
    roots = [b for b in blocks if b.get("uuid") == root["uuid"] and b.get("type") == "part"]
    if len(roots) != 1 or not any(b.get("name") == roots[0].get("pkname")
                                and b.get("type") == "disk" for b in blocks):
        raise RuntimeError("Recovery requires an ordinary, unencrypted system partition")
    binding = json.loads(BINDING.read_text()) if BINDING.exists() else None
    if binding and (binding.get("schema") != 1 or binding.get("root_uuid") != root["uuid"]):
        raise RuntimeError("The recovery binding belongs to another installation")
    volumes = [b for b in blocks if b.get("type") == "part" and b.get("fstype") == "ext4"
               and b.get("label") == "AOS_RECOVERY"
               and (not binding or b.get("uuid") == binding.get("volume_uuid"))]
    if len(volumes) != 1 or volumes[0]["name"] == roots[0]["name"]:
        raise RuntimeError("Prepare one dedicated AOS_RECOVERY ext4 partition first")
    volume = volumes[0]
    if not binding and volume.get("pkname") != roots[0].get("pkname"):
        raise RuntimeError("The unconfigured recovery partition must be on the system disk")
    actual = identity(commands, volume["name"])
    if (actual.get("TYPE") != "ext4" or actual.get("LABEL") != "AOS_RECOVERY"
            or actual.get("UUID") != volume.get("uuid")):
        raise RuntimeError("Recovery partition identity changed. Scan again.")
    result = commands.run("findmnt", "--json", "--output", "TARGET", "--source",
                          volume["name"], check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot determine whether the recovery partition is mounted")
    mounts = json.loads(result.stdout)["filesystems"] if result.stdout.strip() else []
    if mounts:
        steps = "\n".join("  sudo umount -- " + shlex.quote(entry["target"]) for entry in mounts)
        raise RuntimeError("Close file managers and terminals using the recovery partition, then run:\n"
                           "  cd ~\n" + steps + "\nScan again before retrying.")
    return str(UUID(actual["UUID"]))


def install_recovery(commands=None):
    from .recovery_partition import Commands, operation_lock
    commands = commands or Commands(verbose=True)
    with operation_lock():
        require_recovery_volume(commands)
        # Opening/scanning never refreshes APT, and this fixed action does not
        # hide a source refresh either. A missing cache is reported by APT.
        commands.run("apt-get", "install", "-y", "--no-remove", PACKAGE)
        result = commands.run("dpkg-query", "-W", "-f=${db:Status-Abbrev}", PACKAGE)
        if not result.stdout.startswith("ii "):
            raise RuntimeError("Recovery package configuration is incomplete. Check the operation details and retry.")


def repair_boot(commands=None):
    from contextlib import ExitStack
    import fcntl
    import os
    from .recovery_partition import Commands, operation_lock
    commands = commands or Commands(verbose=True)
    with operation_lock(), ExitStack() as locks:
        for path in ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock"):
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            stream = locks.enter_context(os.fdopen(descriptor, "r+"))
            try:
                fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("A package operation is running. Wait for it to finish, then try again.") from error
        volume_uuid = require_recovery_volume(commands)
        commands.run("/usr/libexec/anduinos-recovery-env", "deploy", "--volume-uuid", volume_uuid)
        commands.run("update-grub")
        commands.run("grub-script-check", "/boot/grub/grub.cfg")
        if grub_entry_present() is not True:
            raise RuntimeError("GRUB did not generate the recovery entry. Reinstall anduinos-recovery-env, "
                               "then scan and repair the boot menu again.")
