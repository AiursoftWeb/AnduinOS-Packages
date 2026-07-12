"""Installation backend for the AnduinOS GTK4 installer.

Runs in a background thread.  All operations use subprocess — no debconf,
no partman, no d-i.  Just parted, mkfs.*, unsquashfs, and grub-install.

The public API is a single Installer class:

    installer = Installer(state, log_callback)
    installer.run(on_done_callback)
"""

import os
import sys
import re
import glob
import time
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# Allow absolute imports when run directly (not as a package).
_install_dir = os.path.dirname(os.path.abspath(__file__))
if _install_dir not in sys.path:
    sys.path.insert(0, _install_dir)

from languages import is_chinese, CHINESE_MIRRORS


# ── helpers ──────────────────────────────────────────────────────────────

def _run(cmd: list[str], log: Callable[[str], None],
         check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a command, logging it. Raises RuntimeError on failure when check=True."""
    log(f"  $ {shlex.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
        if r.stdout:
            log(r.stdout.rstrip())
        if check and r.returncode != 0:
            if r.stderr:
                log(r.stderr.rstrip())
            raise RuntimeError(
                f"Command failed with exit code {r.returncode}: {shlex.join(cmd)}"
            )
        return r
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timed out: {shlex.join(cmd)}")


# ── squashfs discovery ───────────────────────────────────────────────────

def find_squashfs(log: Callable[[str], None]) -> str:
    """Find the squashfs file backing the live filesystem.

    On AnduinOS (casper-based), the ISO places the squashfs at
    /cdrom/casper/filesystem.squashfs.  We try several well-known paths,
    then fall back to parsing /proc/mounts.
    """
    # Tier 1: well-known casper paths
    candidates = [
        "/cdrom/casper/filesystem.squashfs",
        "/run/live/medium/casper/filesystem.squashfs",
    ]
    for path in candidates:
        if os.path.isfile(path):
            log(f"SquashFS found at {path}")
            return path

    # Tier 2: find the squashfs mount in /proc/mounts, then losetup
    log("Searching /proc/mounts for squashfs mount point…")
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[2] == "squashfs":
                    losetup = subprocess.check_output(
                        ["losetup", "-n", "-O", "BACK-FILE", parts[0]],
                        text=True, timeout=5,
                    ).strip()
                    if os.path.isfile(losetup):
                        log(f"SquashFS found via losetup: {losetup}")
                        return losetup
    except Exception:
        pass

    raise RuntimeError(
        "Could not find squashfs filesystem. "
        "Ensure the ISO is mounted and /cdrom/casper/filesystem.squashfs exists."
    )


# ── disk operations ──────────────────────────────────────────────────────

def get_partitions(disk_path: str) -> tuple[str, str]:
    """Return (efi_part, root_part) for a freshly partitioned disk.

    After partprobe, we glob the disk path to discover its children.
    Handles both /dev/sda → /dev/sda1 and /dev/nvme0n1 → /dev/nvme0n1p1.
    """
    devname = os.path.basename(disk_path)
    parts = sorted(
        p for p in glob.glob(f"{disk_path}*")
        if os.path.basename(p).startswith(devname) and p != disk_path
    )
    if len(parts) >= 2:
        return parts[0], parts[1]

    # Fallback: predictable naming
    if any(x in devname for x in ("nvme", "mmcblk", "pmem")):
        return f"{disk_path}p1", f"{disk_path}p2"
    return f"{disk_path}1", f"{disk_path}2"


def partition_disk(disk: str, log: Callable[[str], None]):
    """Create GPT partition table: 512 MiB EFI + BTRFS root."""
    log(f"Partitioning {disk}…")
    _run(["parted", "-s", disk, "mklabel", "gpt"], log)
    _run(["parted", "-s", disk, "mkpart", "primary", "fat32", "1MiB", "513MiB"], log)
    _run(["parted", "-s", disk, "mkpart", "primary", "btrfs", "513MiB", "100%"], log)
    _run(["parted", "-s", disk, "set", "1", "esp", "on"], log)
    _run(["partprobe", disk], log)
    time.sleep(2)  # allow udev to create device nodes


def format_partitions(efi: str, root: str, log: Callable[[str], None]):
    """Format EFI as FAT32 and root as BTRFS."""
    log("Formatting partitions…")
    _run(["mkfs.vfat", "-F32", "-n", "EFI", efi], log, timeout=60)
    _run(["mkfs.btrfs", "-f", "-L", "AnduinOS", root], log, timeout=60)


def mount_target(efi: str, root: str, log: Callable[[str], None]):
    """Mount the BTRFS root, create @ subvolume, mount EFI."""
    log("Mounting target filesystem…")
    target = "/target"
    os.makedirs(target, exist_ok=True)
    _run(["mount", "-o", "compress=zstd,noatime", root, target], log)
    _run(["btrfs", "subvolume", "create", f"{target}/@"], log)
    _run(["umount", target], log)
    _run(["mount", "-o", "compress=zstd,noatime,subvol=@", root, target], log)
    efi_dir = f"{target}/boot/efi"
    os.makedirs(efi_dir, exist_ok=True)
    _run(["mount", efi, efi_dir], log)


# ── copy & configure ─────────────────────────────────────────────────────

def copy_filesystem(squashfs: str, log: Callable[[str], None]):
    """Unpack the squashfs into /target."""
    log(f"Copying system files (this may take several minutes)…")
    target = "/target"
    # Exclude files we will regenerate
    excludes = [
        "-e", "tmp/*",
        "-e", "tmp/.*",
        "-e", "var/cache/apt/archives/*",
    ]
    _run(["unsquashfs", "-f", "-d", target, squashfs] + excludes, log,
         timeout=1800)  # 30 minutes


def _get_uuid(dev: str, log) -> str:
    """Get the UUID of a block device."""
    r = _run(["blkid", "-s", "UUID", "-o", "value", dev], log)
    return r.stdout.strip()


def _chroot_run(cmd: list[str], log, check=True, timeout=None):
    """Run a command inside the target chroot."""
    full = ["chroot", "/target"] + cmd
    return _run(full, log, check=check, timeout=timeout)


def configure_system(shared: dict, efi: str, root: str,
                     log: Callable[[str], None]):
    """Write all configuration files into /target."""
    target = Path("/target")
    lang = str(shared.get("lang", "en"))
    hostname = str(shared.get("hostname", "anduinos"))
    username = str(shared.get("username", ""))
    password = str(shared.get("password", ""))
    full_name = str(shared.get("full_name", username))
    timezone = str(shared.get("timezone", "America/New_York"))
    locale_str = "en_US.UTF-8"
    from languages import LANGUAGES
    for l in LANGUAGES:
        if l.code == lang:
            locale_str = l.locale
            break

    # --- fstab ---
    log("Generating fstab…")
    efi_uuid = _get_uuid(efi, log)
    root_uuid = _get_uuid(root, log)
    fstab = (
        f"# /etc/fstab — generated by AnduinOS Installer\n"
        f"UUID={root_uuid} / btrfs defaults,compress=zstd,noatime,subvol=@ 0 0\n"
        f"UUID={efi_uuid} /boot/efi vfat defaults,noatime 0 1\n"
    )
    (target / "etc/fstab").write_text(fstab)

    # --- hostname ---
    log("Setting hostname…")
    (target / "etc/hostname").write_text(hostname + "\n")

    # --- locale ---
    log(f"Setting locale to {locale_str}…")
    (target / "etc/default/locale").write_text(
        f'LANG={locale_str}\n'
        f'LC_ALL={locale_str}\n'
    )
    # Uncomment the locale in locale.gen
    try:
        gen = (target / "etc/locale.gen").read_text()
        gen = gen.replace(f"# {locale_str}", locale_str)
        gen = gen.replace(f"#{locale_str}", locale_str)
        (target / "etc/locale.gen").write_text(gen)
    except Exception:
        log(f"Warning: could not update locale.gen for {locale_str}")

    # --- timezone ---
    log(f"Setting timezone to {timezone}…")
    tz_target = target / "etc/timezone"
    if tz_target.exists() or tz_target.is_symlink():
        tz_target.unlink()
    tz_target.write_text(timezone + "\n")
    tz_link = target / "etc/localtime"
    if tz_link.exists() or tz_link.is_symlink():
        tz_link.unlink()
    os.symlink(f"/usr/share/zoneinfo/{timezone}", str(tz_link))

    # --- user ---
    log(f"Creating user {username}…")
    _chroot_run(["useradd", "-m", "-s", "/bin/bash",
                  "-c", full_name, "-G", "sudo,adm,cdrom,dip,plugdev",
                  username], log, check=False)  # ok if already exists

    # Set password
    log("Setting user password…")
    try:
        import subprocess as sp
        sp.run(
            ["chroot", "/target", "chpasswd"],
            input=f"{username}:{password}",
            text=True, timeout=10, check=False,
        )
    except Exception:
        log("Warning: could not set password via chpasswd")

    # --- Chinese-specific features ---
    if is_chinese(lang):
        _setup_chinese_mirrors(target, log)
        _setup_rime(target, log)

    # --- Cleanup: remove installer from installed system ---
    log("Cleaning up installer files from target…")
    for f in [
        "/usr/bin/anduinos-installer-beta",
        "/usr/share/applications/anduinos-installer-beta.desktop",
    ]:
        p = target / f.lstrip("/")
        if p.exists():
            p.unlink()
            log(f"  Removed {f}")


def _setup_chinese_mirrors(target: Path, log: Callable[[str], None]):
    """Replace archive.ubuntu.com with a Chinese mirror for faster downloads."""
    mirror = CHINESE_MIRRORS[0]
    log(f"Configuring APT to use Chinese mirror: {mirror}")

    old_urls = [
        "http://archive.ubuntu.com/ubuntu/",
        "http://security.ubuntu.com/ubuntu/",
        "https://archive.ubuntu.com/ubuntu/",
        "https://security.ubuntu.com/ubuntu/",
    ]

    def _replace_in_file(path: Path):
        if not path.is_file():
            return
        content = path.read_text()
        changed = False
        for old in old_urls:
            if old in content:
                content = content.replace(old, mirror)
                changed = True
        if changed:
            path.write_text(content)
            log(f"  Updated {path}")

    sources_list = target / "etc/apt/sources.list"
    _replace_in_file(sources_list)

    sources_d = target / "etc/apt/sources.list.d"
    if sources_d.is_dir():
        for f in sources_d.iterdir():
            if f.suffix == ".list" or f.suffix == ".sources":
                _replace_in_file(f)


def _setup_rime(target: Path, log: Callable[[str], None]):
    """Write GSettings override for IBus + Rime (Chinese input)."""
    log("Setting up Chinese input method (Rime)…")
    schema_dir = target / "usr/share/glib-2.0/schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)

    override = schema_dir / "99_anduinos_default_input.gschema.override"
    override.write_text(
        "[org.gnome.desktop.input-sources]\n"
        "sources=[('xkb', 'us'), ('ibus', 'rime')]\n"
    )
    log("  Wrote GSettings override for IBus+Rime")
    # Note: glib-compile-schemas is called in the post-install phase


# ── grub ─────────────────────────────────────────────────────────────────

def install_grub(log: Callable[[str], None]):
    """Install GRUB in UEFI mode on the target system."""
    log("Installing bootloader…")

    # Bind-mount virtual filesystems
    target = "/target"
    bind_mounts = ["/dev", "/proc", "/sys", "/run"]
    for fs in bind_mounts:
        dest = f"{target}{fs}"
        _run(["mount", "--bind", fs, dest], log, check=False)

    try:
        _chroot_run([
            "grub-install",
            "--target=x86_64-efi",
            "--efi-directory=/boot/efi",
            "--bootloader-id=AnduinOS",
            "--recheck",
        ], log, timeout=120)
        _chroot_run(["update-grub"], log, timeout=120)
    finally:
        # Unmount in reverse order
        for fs in reversed(bind_mounts):
            _run(["umount", f"{target}{fs}"], log, check=False)


# ── post-install ─────────────────────────────────────────────────────────

def post_install(log: Callable[[str], None]):
    """Run post-installation tasks inside the target chroot."""
    log("Running post-install tasks…")
    # Generate locales
    _chroot_run(["locale-gen"], log, check=False, timeout=300)
    # Update initramfs
    _chroot_run(["update-initramfs", "-u", "-k", "all"], log,
                check=False, timeout=600)
    # Compile GSettings schemas (needed for Rime override)
    _chroot_run(["glib-compile-schemas", "/usr/share/glib-2.0/schemas/"],
                log, check=False, timeout=30)


def unmount_target(log: Callable[[str], None]):
    """Unmount everything under /target."""
    log("Unmounting target filesystem…")
    target = "/target"
    _run(["umount", "-R", target], log, check=False)


# ── main installer class ─────────────────────────────────────────────────

class Installer:
    """Run the complete installation pipeline.

    The pipeline runs in a background thread.  Callbacks are invoked on
    completion:

        on_done(True, "")   → success
        on_done(False, msg) → failure
    """

    def __init__(self, state: dict, log: Callable[[str], None]):
        self.state = state
        self.log = log
        self.disk = str(state.get("disk", ""))

    def run(self, on_done: Callable[[bool, str], None]):
        """Execute the full install pipeline."""
        try:
            self._pipeline()
            on_done(True, "")
        except Exception as e:
            msg = str(e)
            self.log(f"\nFATAL: {msg}")
            # Try to unmount on error so the target isn't left in a weird state
            try:
                unmount_target(self.log)
            except Exception:
                pass
            on_done(False, msg)

    def _pipeline(self):
        log = self.log

        # 1. Discover squashfs
        log("=== Step 1: Locating system image ===")
        squashfs = find_squashfs(log)

        # 2. Partition
        log(f"\n=== Step 2: Partitioning {self.disk} ===")
        partition_disk(self.disk, log)
        efi, root = get_partitions(self.disk)
        log(f"  EFI partition: {efi}")
        log(f"  Root partition: {root}")

        # 3. Format
        log("\n=== Step 3: Formatting partitions ===")
        format_partitions(efi, root, log)

        # 4. Mount
        log("\n=== Step 4: Mounting target filesystem ===")
        mount_target(efi, root, log)

        # 5. Copy
        log("\n=== Step 5: Copying system files ===")
        copy_filesystem(squashfs, log)

        # 6. Configure
        log("\n=== Step 6: Configuring the system ===")
        configure_system(self.state, efi, root, log)

        # 7. GRUB
        log("\n=== Step 7: Installing bootloader ===")
        install_grub(log)

        # 8. Post-install
        log("\n=== Step 8: Post-install tasks ===")
        post_install(log)

        # 9. Unmount
        log("\n=== Step 9: Unmounting ===")
        unmount_target(log)

        log("\n✓ Installation complete!")
