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
import time
from datetime import datetime, timezone
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


def target(volume_uuid=None, *, if_present=False):
    binding = read_binding(STATE) if STATE.exists() else None
    root_mount = run("findmnt", "--json", "--output", "FSTYPE,UUID,FSROOT", "--target", "/", check=False)
    if not root_mount:
        if if_present and binding is None:
            return None  # An image assembly chroot need not have its own mount.
        raise RuntimeError("Cannot identify the installed root filesystem")
    root = json.loads(root_mount)["filesystems"][0]
    if root["fstype"] != "btrfs" or root["fsroot"] != "/@root":
        if if_present and binding is None:
            return None  # Building the ISO or running Live must never deploy.
        raise RuntimeError("Recovery requires an AnduinOS Btrfs root using @root")
    root_uuid = read_uuid(root["uuid"])
    root_devices = run("blkid", "-c", "/dev/null", "-t", f"UUID={root_uuid}", "-o", "device").splitlines()
    if len(root_devices) != 1:
        raise RuntimeError("Recovery requires a single, unambiguous root device")
    # Encrypted/mapped storage needs a separate unlock stage, not a guessed device.
    types = run("lsblk", "-s", "-n", "-o", "TYPE", root_devices[0]).split()
    if any(kind not in ("disk", "part") for kind in types):
        raise RuntimeError("This recovery version requires an unencrypted disk partition")
    if volume_uuid is not None:
        volume_uuid = read_uuid(volume_uuid)
        if binding is not None and binding["volume_uuid"] != volume_uuid:
            raise RuntimeError("Requested recovery volume differs from the installed binding")
    selected_uuid = volume_uuid or (binding["volume_uuid"] if binding else None)
    query = f"UUID={selected_uuid}" if selected_uuid else f"LABEL={LABEL}"
    devices = run("blkid", "-c", "/dev/null", "-t", query, "-o", "device", check=False).splitlines()
    if not devices and if_present and binding is None and volume_uuid is None:
        return None
    if len(devices) != 1:
        raise RuntimeError(f"Expected one dedicated {LABEL} recovery volume; found {len(devices)}")
    device = devices[0]
    if if_present and binding is None:
        # Automatic discovery must not claim a spare volume on another disk.
        root_disks = run("lsblk", "--raw", "-s", "-n", "-o", "NAME,TYPE", "-p", root_devices[0]).splitlines()
        volume_disks = run("lsblk", "--raw", "-s", "-n", "-o", "NAME,TYPE", "-p", device).splitlines()
        disks = lambda rows: {row.split()[0] for row in rows if row.split()[-1] == "disk"}
        if disks(root_disks) != disks(volume_disks) or not disks(root_disks):
            raise RuntimeError("Automatic recovery deployment requires a volume on the system disk")
    info = dict(line.split("=", 1) for line in run("blkid", "-p", "-o", "export", device).splitlines() if "=" in line)
    if info.get("TYPE") != "ext4" or info.get("LABEL") != LABEL:
        raise RuntimeError(f"Recovery volume must be ext4 and labelled {LABEL}")
    selected = {"schema": 1, "root_uuid": root_uuid, "volume_uuid": read_uuid(info["UUID"])}
    if binding is not None and binding != selected:
        raise RuntimeError("Recovery volume belongs to another installation")
    if run("findmnt", "-n", "-S", device, check=False):
        raise RuntimeError("Unmount the recovery volume before deploying it")
    return device, selected


def deploy(volume_uuid=None, *, if_present=False):
    if os.geteuid() != 0:
        raise RuntimeError("Recovery deployment must run as root")
    selected = target(volume_uuid, if_present=if_present)
    if selected is None:
        print("No recovery deployment is configured for this system.")
        return
    verify_payload(PAYLOAD)
    device, binding = selected
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
    print(f'''menuentry 'AnduinOS Recovery Environment' --class recovery --id anduinos-recovery-env {{
    insmod part_gpt
    insmod ext2
    search --no-floppy --fs-uuid --set=recovery {binding["volume_uuid"]}
    linux ($recovery)/{DIRECTORY}/current/vmlinuz anduinos.recovery.root={binding["root_uuid"]} console=tty0 loglevel=3
    initrd ($recovery)/{DIRECTORY}/current/initrd.img
}}''')


def bounded_json(path, limit=1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError(f"Invalid recovery record: {path}")
    return json.loads(path.read_text())


def boot_candidate(store, started):
    """Use the current branch, not a newer snapshot from an abandoned branch."""
    lineage = bounded_json(store / "history/system-lineage.json", 4 * 1024 * 1024)
    if lineage["schema_version"] != 1:
        raise RuntimeError("Unsupported system snapshot history")
    nodes = {node["recovery_point_id"]: node for node in lineage["nodes"]}
    head, seen = lineage["current_head_id"], set()
    while head:
        head = read_uuid(head)
        if head in seen:
            raise RuntimeError("Snapshot history contains a cycle")
        seen.add(head)
        node = nodes[head]
        if node["snapshot_available"] and datetime.fromisoformat(node["created_at"]) <= started:
            record = bounded_json(store / "metadata" / (head + ".json"))
            if (record["schema_version"] == 1 and record["id"] == head
                    and record["state"] == "ready" and not record.get("failure")
                    and datetime.fromisoformat(record["created_at"]) <= started):
                read_uuid(record["snapshot_uuid"])
                return record
        # Legacy history cannot prove ancestry; leave the badge absent.
        head = node["parent_id"] if node["relation"] == "exact" else None
    return None


def graphical_session_ready():
    for line in run("loginctl", "list-sessions", "--no-legend", check=False).splitlines():
        fields = run("loginctl", "show-session", line.split()[0],
                     "-p", "Type", "-p", "State", "-p", "Class", "-p", "Remote", check=False)
        state = dict(part.split("=", 1) for part in fields.splitlines() if "=" in part)
        if (state.get("Type") in ("x11", "wayland") and state.get("State") == "active"
                and state.get("Class") in ("greeter", "user") and state.get("Remote") == "no"):
            return True
    return False


def record_boot():
    """One advisory checkpoint per successful boot; never write to recovery media."""
    binding = read_binding(STATE)
    root = json.loads(run("findmnt", "--json", "-o", "FSTYPE,UUID,FSROOT", "--target", "/"))["filesystems"][0]
    snapshots = json.loads(run("findmnt", "--json", "-o", "FSTYPE,UUID,FSROOT", "--target", "/.snapshots"))["filesystems"][0]
    if (root["fstype"] != "btrfs" or root["fsroot"] != "/@root"
            or snapshots["fsroot"] != "/@snapshots"
            or root["uuid"] != binding["root_uuid"] or snapshots["uuid"] != root["uuid"]):
        raise RuntimeError("The snapshot store does not belong to the configured system")
    store = Path("/.snapshots/anduinos-btrfs-snapshots-manager")
    if store.resolve() != store:
        raise RuntimeError("The snapshot store must not be a symbolic link")
    if any((store / name).exists() for name in ("transactions/pending-rollback.json", "offline-transactions/pending.json")):
        return  # Unfinished recovery is not evidence of a successful new boot.
    marker = store / "last-successful-boot.json"
    boot_id = read_uuid(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    if marker.exists():
        try:
            if bounded_json(marker, 4096).get("boot_id") == boot_id:
                return
        except (OSError, ValueError, RuntimeError):
            pass  # A damaged advisory marker may be replaced by fresh evidence.
    epoch = next(line.split()[1] for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))
    record = boot_candidate(store, datetime.fromtimestamp(int(epoch), timezone.utc))
    if record is None:
        return
    for _ in range(120):
        if graphical_session_ready():
            break
        time.sleep(5)
    else:
        return  # Failed or non-graphical boot: preserve the previous checkpoint.
    snapshot = store / "deployments" / record["id"] / "root"
    if snapshot.resolve() != snapshot:
        raise RuntimeError("Snapshot path must not contain symbolic links")
    actual = re.search(r"(?m)^\s*UUID:\s*(\S+)", run("btrfs", "subvolume", "show", str(snapshot)))
    if not actual or actual[1] != record["snapshot_uuid"] or run("btrfs", "property", "get", "-ts", str(snapshot), "ro") != "ro=true":
        raise RuntimeError("The boot checkpoint snapshot is missing or has changed")
    atomic_json(marker, {"schema": 1, "root_uuid": root["uuid"], "boot_id": boot_id,
                         "snapshot_id": record["id"], "snapshot_uuid": record["snapshot_uuid"]})
    run("sync", "-f", str(store))
    print("Recorded the recovery point available at the last successful graphical boot.")


if __name__ == "__main__":
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "deploy":
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command", choices=("deploy",))
            parser.add_argument("--volume-uuid")
            parser.add_argument("--if-present", action="store_true")
            args = parser.parse_args()
            with open("/run/anduinos-recovery-env.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deploy(args.volume_uuid, if_present=args.if_present)
        elif sys.argv[1:] == ["grub"]:
            grub()
        elif sys.argv[1:] == ["record-boot"]:
            with open("/run/anduinos-recovery-boot.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                record_boot()
        else:
            raise RuntimeError("Usage: anduinos-recovery-env {deploy|grub|record-boot}")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"AnduinOS recovery: {error}", file=sys.stderr)
        sys.exit(1)
