"""Read-only recovery checks using disk metadata and the local APT cache."""

from dataclasses import dataclass
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
    if grub_entry is None:
        result = runner.run(["pkexec", "--disable-internal-agent", PROBE])
        if result.returncode == 0:
            try:
                value = json.loads(result.stdout)["grub_entry"]
                if type(value) is bool:
                    grub_entry = value
            except (ValueError, KeyError, TypeError):
                pass
    return RecoveryState(partition, devices, package, candidate, up_to_date, grub_entry)
