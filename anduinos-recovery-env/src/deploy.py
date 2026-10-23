#!/usr/bin/python3
"""Deploy only to the explicitly provisioned recovery filesystem."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID


LABEL = "AOS_RECOVERY"
PAYLOAD = Path("/usr/lib/anduinos-recovery-env")
STATE = Path("/var/lib/anduinos-recovery-env/volume.json")
DIRECTORY = "anduinos-recovery-env"
FILES = ("vmlinuz", "initrd.img")


def run(*args, check=True):
    result = subprocess.run(args, check=check, text=True, capture_output=True)
    return result.stdout.strip()


def read_uuid(value):
    return str(UUID(value))


def read_binding(path):
    value = json.loads(path.read_text())
    if value.get("schema") != 1:
        raise RuntimeError("Unsupported recovery volume binding")
    return {"schema": 1, "root_uuid": read_uuid(value["root_uuid"]),
            "volume_uuid": read_uuid(value["volume_uuid"])}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_payload(path):
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("schema") != 1 or set(manifest.get("files", {})) != set(FILES):
        raise RuntimeError("Invalid recovery payload manifest")
    if manifest.get("architecture") != run("dpkg", "--print-architecture"):
        raise RuntimeError("Recovery payload architecture differs from the installed system")
    for name in FILES:
        file = path / name
        if file.is_symlink() or not file.is_file() or digest(file) != manifest["files"][name]:
            raise RuntimeError(f"Recovery payload checksum failed: {name}")
    return manifest


def target():
    root = json.loads(run("findmnt", "--json", "--output", "FSTYPE,UUID,FSROOT", "--target", "/"))["filesystems"][0]
    if root["fstype"] != "btrfs" or root["fsroot"] != "/@root":
        raise RuntimeError("Recovery requires an AnduinOS Btrfs root using @root")
    root_uuid = read_uuid(root["uuid"])
    root_devices = run("blkid", "-c", "/dev/null", "-t", f"UUID={root_uuid}", "-o", "device").splitlines()
    if len(root_devices) != 1:
        raise RuntimeError("Recovery requires a single, unambiguous root device")
    # Encrypted/mapped storage needs a separate unlock stage, not a guessed device.
    types = run("lsblk", "-s", "-n", "-o", "TYPE", root_devices[0]).split()
    if any(kind not in ("disk", "part") for kind in types):
        raise RuntimeError("This recovery version requires an unencrypted disk partition")
    binding = read_binding(STATE) if STATE.exists() else None
    query = f"UUID={binding['volume_uuid']}" if binding else f"LABEL={LABEL}"
    devices = run("blkid", "-c", "/dev/null", "-t", query, "-o", "device", check=False).splitlines()
    if len(devices) != 1:
        raise RuntimeError(f"Expected one dedicated {LABEL} recovery volume; found {len(devices)}")
    device = devices[0]
    info = dict(line.split("=", 1) for line in run("blkid", "-p", "-o", "export", device).splitlines() if "=" in line)
    if info.get("TYPE") != "ext4" or info.get("LABEL") != LABEL:
        raise RuntimeError(f"Recovery volume must be ext4 and labelled {LABEL}")
    selected = {"schema": 1, "root_uuid": root_uuid, "volume_uuid": read_uuid(info["UUID"])}
    if binding is not None and binding != selected:
        raise RuntimeError("Recovery volume belongs to another installation")
    if run("findmnt", "-n", "-S", device, check=False):
        raise RuntimeError("Unmount the recovery volume before deploying it")
    return device, selected


def deploy():
    if os.geteuid() != 0:
        raise RuntimeError("Recovery deployment must run as root")
    verify_payload(PAYLOAD)
    device, binding = target()
    mount = Path(tempfile.mkdtemp(prefix="anduinos-recovery-", dir="/run"))
    try:
        run("mount", "-t", "ext4", "-o", "nodev,nosuid,noexec", device, str(mount))
        try:
            owned = mount / DIRECTORY
            if owned.is_symlink():
                raise RuntimeError("Recovery directory must not be a symbolic link")
            if owned.exists():
                if read_binding(owned / "owner.json") != binding:
                    raise RuntimeError("Recovery files belong to another installation")
            elif any(p.name != "lost+found" for p in mount.iterdir()):
                raise RuntimeError("The recovery volume is not empty; refusing to overwrite it")
            owned.mkdir(exist_ok=True)
            atomic_json(owned / "owner.json", binding)
            required = sum((PAYLOAD / name).stat().st_size for name in (*FILES, "manifest.json")) + 4 * 1024 * 1024
            if shutil.disk_usage(mount).free < required:
                raise RuntimeError("Not enough recovery space to stage an update while preserving the current image")
            candidate = Path(tempfile.mkdtemp(prefix="payload-", dir=owned))
            try:
                for name in (*FILES, "manifest.json"):
                    shutil.copyfile(PAYLOAD / name, candidate / name)
                    with (candidate / name).open("rb") as stream:
                        os.fsync(stream.fileno())
                candidate.chmod(0o755)
                verify_payload(candidate)
                run("sync", "-f", str(mount))
                # One directory pointer selects both files. Never delete the old
                # image before the complete replacement has been read back.
                link = owned / "current.new"
                link.unlink(missing_ok=True)
                link.symlink_to(candidate.name)
                os.replace(link, owned / "current")
                run("sync", "-f", str(mount))
            except BaseException:
                if not (owned / "current").is_symlink() or os.readlink(owned / "current") != candidate.name:
                    shutil.rmtree(candidate)
                raise
            for old in owned.iterdir():
                if old != candidate and re.fullmatch(r"payload-[a-z0-9_]{8}", old.name) and not old.is_symlink():
                    shutil.rmtree(old)
            run("sync", "-f", str(mount))
        finally:
            run("umount", str(mount))
    finally:
        # Never recursively clean a mount point: a failed unmount must leave
        # the volume and its evidence intact.
        if not os.path.ismount(mount):
            mount.rmdir()
    atomic_json(STATE, binding)
    run("sync", "-f", str(STATE.parent))
    print("Recovery environment deployed and volume unmounted.")


def grub():
    if not STATE.exists():
        return  # Installing other packages before initial deployment is harmless.
    binding = read_binding(STATE)
    print(f'''menuentry 'AnduinOS Recovery Environment' --id anduinos-recovery-env {{
    insmod part_gpt
    insmod ext2
    search --no-floppy --fs-uuid --set=recovery {binding["volume_uuid"]}
    linux ($recovery)/{DIRECTORY}/current/vmlinuz anduinos.recovery.root={binding["root_uuid"]} loglevel=3
    initrd ($recovery)/{DIRECTORY}/current/initrd.img
}}''')


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["deploy"]:
            with open("/run/anduinos-recovery-env.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deploy()
        elif sys.argv[1:] == ["grub"]:
            grub()
        else:
            raise RuntimeError("Usage: anduinos-recovery-env {deploy|grub}")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"AnduinOS recovery: {error}", file=sys.stderr)
        sys.exit(1)
