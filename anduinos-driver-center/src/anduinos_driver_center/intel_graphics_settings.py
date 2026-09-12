"""Small, restricted GRUB configuration transaction for Intel graphics."""
from __future__ import annotations

import fcntl
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import tempfile

from .intel_graphics import DRIVERS, PARAMETERS, can_switch, discover, module_parameters, read, run

CONFIG = Path("etc/default/grub.d/99-anduinos-intel-graphics.cfg")
HEADER = "# Managed by AnduinOS Driver Center.\n"
PREFIX = 'GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT}'
MENU = ('\nGRUB_TIMEOUT_STYLE=menu\n'
        'case "$GRUB_TIMEOUT" in ""|0|1|2|3|4) GRUB_TIMEOUT=5 ;; esac\n'
        'GRUB_CMDLINE_LINUX_RECOVERY="systemd.unit=multi-user.target"\n')
TOKEN = re.compile(r"(?:i915|xe)\.(?:enable_psr|enable_fbc|enable_dc|enable_panel_replay)=0|(?:i915|xe)\.force_probe=!?[0-9a-f]{4}(?:,!?[0-9a-f]{4})*")
CONFLICT = re.compile(r"\b(?:i915|xe)\.(?:enable_\w+|force_probe|modeset)\b|\bnomodeset\b|\b(?:options|blacklist|install|softdep)\s+(?:i915|xe)\b|(?:blacklist|driver\.pre)[^\n]*\b(?:i915|xe)\b|\bGRUB_CMDLINE_LINUX_RECOVERY\s*=")


def render(tokens: list[str]) -> str:
    if any(not TOKEN.fullmatch(token) for token in tokens) or len(tokens) != len({token.partition("=")[0] for token in tokens}):
        raise ValueError("Invalid Intel graphics parameters")
    if not tokens:
        return ""
    return HEADER + PREFIX + " " + " ".join(sorted(tokens)) + '"\n' + MENU


def parse(content: str) -> list[str]:
    if not content:
        return []
    lines = content.splitlines()
    if len(lines) < 2 or not lines[1].startswith(PREFIX + " ") or not lines[1].endswith('"'):
        raise ValueError("The managed Intel graphics file was modified outside Driver Center")
    tokens = lines[1][len(PREFIX) + 1:-1].split()
    if render(tokens) != content:
        raise ValueError("The managed Intel graphics file was modified outside Driver Center")
    return tokens


def installed_kernels(root: Path) -> list[str]:
    return sorted(path.name.removeprefix("vmlinuz-") for path in (root / "boot").glob("vmlinuz-*")
                  if re.fullmatch(r"vmlinuz-[A-Za-z0-9.+_-]+", path.name))


def conflicts(root: Path) -> list[str]:
    paths = [root / "etc/default/grub"]
    for directory, pattern in (("etc/default/grub.d", "*.cfg"), ("etc/modprobe.d", "*.conf"),
                               ("run/modprobe.d", "*.conf"), ("usr/lib/modprobe.d", "*.conf"),
                               ("lib/modprobe.d", "*.conf")):
        paths.extend((root / directory).glob(pattern))
    found = set()
    for path in paths:
        if path == root / CONFIG:
            continue
        for line in read(path).splitlines():
            if not line.lstrip().startswith("#") and CONFLICT.search(line):
                found.add("/" + str(path.relative_to(root)))
    return sorted(found)


def saved(root: Path = Path("/")) -> list[str]:
    path = root / CONFIG
    if path.is_symlink():
        raise ValueError("The managed Intel graphics file must not be a symbolic link")
    try:
        return parse(path.read_text())
    except FileNotFoundError:
        return []


def status(root: Path = Path("/")) -> dict:
    tokens = saved(root)
    running = read(root / "proc/cmdline").split()
    running = [token for token in running if TOKEN.fullmatch(token)]
    blocked = conflicts(root)
    unexpected = [token for token in read(root / "proc/cmdline").split()
                  if (token.startswith(("i915.", "xe.")) or token == "nomodeset") and token not in tokens]
    return {"tokens": tokens, "running": running, "pending": set(tokens) != set(running),
            "conflicts": blocked, "external_boot_parameters": unexpected,
            "interrupted": (root / "var/lib/anduinos-driver-center/intel-graphics/pending").exists()}


def plan(request: dict, root: Path = Path("/"), runner=run) -> list[str]:
    if not isinstance(request, dict) or set(request) != {"settings", "switches"}:
        raise ValueError("Invalid Intel graphics request")
    settings, switches = request["settings"], request["switches"]
    if not isinstance(settings, dict) or not isinstance(switches, dict):
        raise ValueError("Invalid Intel graphics request")
    devices = discover(root, runner)
    if not devices:
        raise ValueError("No Intel graphics device was found")
    kernels = installed_kernels(root)
    if not kernels:
        raise ValueError("No installed boot kernel was found")
    active = {device.driver for device in devices} & set(DRIVERS)
    result = []
    for key, value in settings.items():
        driver, _, parameter = key.partition(".")
        if driver not in active or parameter not in PARAMETERS or value not in ("auto", "disabled"):
            raise ValueError("Unsupported Intel graphics setting")
        if value == "disabled":
            if not all(parameter in module_parameters(driver, kernel, runner) for kernel in kernels):
                raise ValueError("The setting is unavailable in an installed kernel")
            result.append(f"{driver}.{parameter}=0")
    per_driver = {driver: [] for driver in DRIVERS}
    for address, target in switches.items():
        matches = [device for device in devices if device.address == address]
        if len(matches) != 1 or target not in (*DRIVERS, "default"):
            raise ValueError("Invalid Intel graphics driver selection")
        if target == "default":
            continue
        device = matches[0]
        if not can_switch(device, target, kernels, runner):
            raise ValueError("This driver and hardware combination has not passed acceptance")
        if sum(other.device_id == device.device_id for other in devices) != 1:
            raise ValueError("Driver switching affects multiple devices with this PCI ID")
        if target != device.driver and any(key.startswith(device.driver + ".") and value == "disabled"
                                           for key, value in settings.items()):
            raise ValueError("Restore display settings before switching drivers")
        for driver in DRIVERS:
            per_driver[driver].append(("" if driver == target else "!") + device.device_id)
    for driver, ids in per_driver.items():
        if ids:
            result.append(f"{driver}.force_probe={','.join(sorted(ids))}")
    render(result)
    return sorted(result)


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".intel-graphics-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def boot_entries(generated: str) -> tuple[list[list[str]], list[list[str]]]:
    # Other Linux installations and Disk Snapshots Manager have their own
    # entries. Only the installed system's 10_linux entries inherit DEFAULT.
    begin = "### BEGIN /etc/grub.d/10_linux ###"
    end = "### END /etc/grub.d/10_linux ###"
    if begin in generated:
        generated = generated.split(begin, 1)[1]
        if end not in generated:
            raise RuntimeError("The generated Linux boot section is incomplete")
        generated = generated.split(end, 1)[0]
    normal, recovery = [], []
    for line in generated.splitlines():
        if not re.match(r"\s*linux(?:efi)?\s", line):
            continue
        words = line.split()
        is_recovery = bool(set(words) & {"recovery", "single", "systemd.unit=rescue.target", "systemd.unit=emergency.target", "systemd.unit=multi-user.target"})
        (recovery if is_recovery else normal).append(words)
    return normal, recovery


def boot_run(command: list[str]):
    return run(command, timeout=120)


def apply(request: dict | None, root: Path = Path("/"), runner=boot_run) -> None:
    """None restores only our overrides. root/runner are internal UT seams."""
    config = root / CONFIG
    grub = root / "boot/grub/grub.cfg"
    state = root / "var/lib/anduinos-driver-center/intel-graphics"
    for path in (config, grub, state):
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
            raise ValueError("Symbolic links are not supported for managed boot configuration")
    if not grub.is_file() or not config.parent.is_dir():
        raise ValueError("An installed GRUB system is required")
    if (root / "run/initramfs/live").exists() or (root / "run/live/medium").exists() or (root / "cdrom/casper").exists():
        raise ValueError("Persistent graphics settings are unavailable in a Live session")
    # Backups are 0600; traversal permits the UI to detect the pending marker.
    state.mkdir(parents=True, exist_ok=True, mode=0o755)
    lock = state / "lock"
    with ExitStack() as locks:
        stream = locks.enter_context(os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w"))
        fcntl.flock(stream, fcntl.LOCK_EX)
        # Kernel package triggers also regenerate GRUB. Use dpkg's POSIX lock
        # protocol so an APT upgrade cannot overlap our boot-file transaction.
        for name in ("lock-frontend", "lock"):
            path = root / "var/lib/dpkg" / name
            if path.exists():
                package_lock = locks.enter_context(os.fdopen(os.open(path, os.O_RDWR | os.O_NOFOLLOW), "r+"))
                try:
                    fcntl.lockf(package_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError("A package operation is running; try again after it finishes") from error
        saved(root)  # Reject foreign changes even during reset.
        before = config.read_bytes() if config.exists() else None
        old_grub = grub.read_bytes()
        tokens = plan(request, root, runner) if request is not None else []
        if request is not None:
            info = status(root)
            if info["interrupted"]:
                raise ValueError("A previous graphics operation was interrupted; restore defaults first")
            if info["conflicts"] or info["external_boot_parameters"]:
                raise ValueError("External graphics overrides must be resolved before applying settings")
        record = json.dumps({"config": before.decode() if before is not None else None})
        atomic_write(state / "previous.json", record.encode())
        atomic_write(state / "previous-grub.cfg", old_grub)
        fd, candidate_name = tempfile.mkstemp(prefix=".intel-graphics-grub-", dir=grub.parent)
        os.close(fd)
        candidate = Path(candidate_name)
        try:
            atomic_write(state / "pending", b"Restore defaults if this operation is interrupted.\n")
            if tokens:
                atomic_write(config, render(tokens).encode(), 0o644)
            else:
                config.unlink(missing_ok=True)
            for command in (["grub-mkconfig", "-o", str(candidate)], ["grub-script-check", str(candidate)]):
                result = runner(command)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout or "GRUB configuration failed")[-8000:])
            generated = candidate.read_text()
            normal, recovery = boot_entries(generated)
            if not normal or (tokens and not recovery) or any(not all(token in line for token in tokens) for line in normal):
                raise RuntimeError("GRUB did not produce the expected normal and recovery entries")
            if tokens and any(any(TOKEN.fullmatch(token) for token in line) for line in recovery):
                raise RuntimeError("The recovery entry contains graphics overrides")
            if tokens and any("systemd.unit=multi-user.target" not in line or "nomodeset" not in line for line in recovery):
                raise RuntimeError("Recovery must provide normal-user console login with safe graphics")
            atomic_write(grub, candidate.read_bytes(), grub.stat().st_mode & 0o777)
            (state / "pending").unlink()
        except Exception:
            if before is None:
                config.unlink(missing_ok=True)
            else:
                atomic_write(config, before, 0o644)
            atomic_write(grub, old_grub, grub.stat().st_mode & 0o777)
            (state / "pending").unlink(missing_ok=True)
            raise
        finally:
            candidate.unlink(missing_ok=True)
            candidate.with_name(candidate.name + ".new").unlink(missing_ok=True)
