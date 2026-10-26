"""Prepare one recovery partition from the installed system's ordinary GPT swap.

The fixed privileged helper always discovers the current root itself. UI input
is an expected plan, never a caller-selected device or partition table.
"""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from uuid import UUID, uuid4

MIB = 1024 * 1024
RECOVERY_BYTES = 256 * MIB
MIN_SWAP_BYTES = 512 * MIB
SWAP_TYPE = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"
LINUX_TYPE = "0fc63daf-8483-4772-8e79-3d69d8477de4"
STATE = Path("/var/lib/anduinos-driver-center/recovery-partition")
LOCK = Path("/run/lock/anduinos-recovery-prepare.lock")
BINDING = Path("/var/lib/anduinos-recovery-env/volume.json")
TOOLS = ("findmnt", "lsblk", "sfdisk", "partx", "blkid", "blockdev", "swapon",
         "swapoff", "mkswap", "mkfs.ext4", "e2fsck", "udevadm")


class Commands:
    def __init__(self, verbose=False):
        self.verbose = verbose

    def run(self, *arguments, data=None, check=True):
        if self.verbose:
            print("+ " + " ".join(arguments), flush=True)
        result = subprocess.run(arguments, input=data, capture_output=True, text=True,
                                timeout=60, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
        if self.verbose:
            print(result.stdout + result.stderr, end="", flush=True)
        if check and result.returncode:
            raise RuntimeError(f"{arguments[0]} failed: {(result.stderr or result.stdout)[-4000:]}")
        return result

    def text(self, *arguments):
        return self.run(*arguments).stdout.strip()


def canonical(table):
    value = copy.deepcopy(table)
    for key in ("id", "uuid", "type"):
        if isinstance(value.get(key), str):
            value[key] = value[key].lower()
    if "partitions" in value:
        value["partitions"] = [canonical(part) for part in value["partitions"]]
    return value


def table_hash(table):
    return hashlib.sha256(json.dumps(canonical(table), sort_keys=True).encode()).hexdigest()


def partition_number(disk, device):
    prefix = disk + ("p" if disk[-1].isdigit() else "")
    suffix = device.removeprefix(prefix)
    if not device.startswith(prefix) or not suffix.isdigit() or int(suffix) < 1:
        raise ValueError("Unsupported partition device name")
    return int(suffix)


def make_plan(root, blocks, table, identity, swaps, available_memory):
    """Command-free eligibility and exact geometry; shared by preview and execution."""
    if root.get("fstype") != "btrfs" or root.get("fsroot") != "/@root":
        return {"reason": "unsupported-root"}
    roots = [b for b in blocks if b.get("uuid") == root.get("uuid") and b.get("type") == "part"]
    if len(roots) != 1:
        return {"reason": "unsupported-root"}
    root_device = roots[0]
    disks = [b for b in blocks if b.get("name") == root_device.get("pkname") and b.get("type") == "disk"]
    if len(disks) != 1:
        return {"reason": "unsupported-root"}
    disk = disks[0]
    if any(b.get("label") == "AOS_RECOVERY" for b in blocks):
        return {"reason": "existing-volume"}
    candidates = [b for b in blocks if b.get("pkname") == disk["name"]
                  and b.get("type") == "part" and b.get("fstype") == "swap"]
    if not candidates:
        return {"reason": "no-swap"}
    if len(candidates) != 1:
        return {"reason": "multiple-swaps"}
    swap = candidates[0]
    if int(swap["size"]) < MIN_SWAP_BYTES:
        return {"reason": "small-swap"}
    sector = table.get("sectorsize")
    if table.get("label") != "gpt" or sector not in (512, 4096) or table.get("device") != disk["name"]:
        return {"reason": "unsupported-layout"}
    parts = table["partitions"]
    selected = next((p for p in parts if p["node"] == swap["name"]), None)
    if (not selected or selected["type"].lower() != SWAP_TYPE
            or selected["size"] * sector != int(swap["size"])
            or (selected["start"] + selected["size"]) * sector % MIB):
        return {"reason": "unsupported-layout"}
    numbers = {partition_number(disk["name"], p["node"]) for p in parts}
    number = next((n for n in range(1, 129) if n not in numbers), None)
    if number is None or len(parts) != len(numbers):
        return {"reason": "unsupported-layout"}
    ranges = sorted((p["start"], p["start"] + p["size"]) for p in parts)
    if (not ranges or ranges[0][0] < table["firstlba"]
            or ranges[-1][1] > table["lastlba"] + 1
            or any(a[1] > b[0] for a, b in zip(ranges, ranges[1:]))):
        return {"reason": "unsupported-layout"}
    if (identity.get("TYPE") != "swap" or identity.get("UUID") != swap.get("uuid")
            or identity.get("LABEL", "") != (swap.get("label") or "")):
        return {"reason": "inspection-failed"}
    # Validate identifiers before any plan can be approved.
    for value in (root["uuid"], table["id"], selected["uuid"], identity["UUID"]):
        UUID(value)
    active = [s for s in swaps if os.path.realpath(s["name"]) == swap["name"]]
    if len(active) > 1:
        return {"reason": "inspection-failed"}
    priority = int(active[0]["prio"]) if active else None
    # Kernel-assigned negative priorities cannot be explicitly restored exactly.
    if priority is not None and not 0 <= priority <= 32767:
        return {"reason": "swap-priority"}
    if active and available_memory < int(active[0]["used"]) + 64 * MIB:
        return {"reason": "memory"}
    prefix = disk["name"] + ("p" if disk["name"][-1].isdigit() else "")
    plan = {
        "disk": disk["name"], "disk_bytes": int(disk["size"]), "disk_id": disk["maj:min"],
        "root_uuid": root["uuid"], "table_hash": table_hash(table), "sector_bytes": sector,
        "swap": swap["name"], "swap_id": swap["maj:min"], "swap_uuid": identity["UUID"],
        "swap_label": identity.get("LABEL", ""), "swap_number": partition_number(disk["name"], swap["name"]),
        "swap_start": selected["start"], "swap_bytes": int(swap["size"]),
        "remaining_bytes": int(swap["size"]) - RECOVERY_BYTES,
        "active": bool(active), "priority": priority,
        "new_number": number, "new_device": prefix + str(number),
        "new_start": selected["start"] + selected["size"] - RECOVERY_BYTES // sector,
    }
    return {"reason": "ready", "plan": plan, "table": table}


def read_table(commands, disk):
    return json.loads(commands.text("sfdisk", "--json", disk))["partitiontable"]


def identity(commands, device):
    return dict(line.split("=", 1) for line in commands.text(
        "blkid", "-c", "/dev/null", "-p", "-o", "export", device).splitlines() if "=" in line)


def active_swaps():
    # /proc/swaps is available on every supported kernel; swapon has no JSON
    # interface on supported Ubuntu versions. Names escape whitespace as octal.
    result = []
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        name, kind, size, used, priority = line.split()
        name = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), name)
        result.append({"name": name, "type": kind, "size": int(size) * 1024,
                       "used": int(used) * 1024, "prio": int(priority)})
    return result


def inspect_partition(commands=None):
    commands = commands or Commands()
    if (STATE / "pending.json").exists():
        return {"reason": "interrupted", "backup": str(STATE)}
    if BINDING.exists():
        return {"reason": "bound-volume"}
    if any(shutil.which(tool) is None for tool in TOOLS):
        return {"reason": "missing-tools"}
    try:
        root = json.loads(commands.text("findmnt", "--json", "--output", "FSTYPE,FSROOT,UUID",
                                        "--target", "/"))["filesystems"][0]
        blocks = json.loads(commands.text("lsblk", "--json", "--bytes", "--paths", "--list",
            "--output", "NAME,TYPE,FSTYPE,LABEL,UUID,PKNAME,SIZE,MAJ:MIN"))["blockdevices"]
        roots = [b for b in blocks if b.get("uuid") == root.get("uuid") and b.get("type") == "part"]
        if len(roots) != 1 or not roots[0].get("pkname"):
            return {"reason": "unsupported-root"}
        disk = roots[0]["pkname"]
        candidates = [b for b in blocks if b.get("pkname") == disk and b.get("fstype") == "swap"
                      and b.get("type") == "part"]
        # make_plan supplies the specific no-swap/small/ambiguous explanation.
        info = identity(commands, candidates[0]["name"]) if len(candidates) == 1 else {}
        memory = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("MemAvailable:"))
        return make_plan(root, blocks, read_table(commands, disk), info, active_swaps(), memory)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError, subprocess.SubprocessError):
        return {"reason": "inspection-failed"}


def geometry(device):
    path = Path("/sys/class/block") / Path(device).name
    if not path.exists():
        return None
    node = os.stat(device)
    if not stat.S_ISBLK(node.st_mode) or (path / "dev").read_text().strip() != f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}":
        raise RuntimeError("Partition device identity changed")
    return {"start": int((path / "start").read_text()) * 512,
            "size": int((path / "size").read_text()) * 512}


def verify_geometry(table):
    for part in table["partitions"]:
        expected = {"start": part["start"] * table["sectorsize"],
                    "size": part["size"] * table["sectorsize"]}
        if geometry(part["node"]) != expected:
            raise RuntimeError(f"Kernel partition geometry does not match the plan: {part['node']}")


def verify_disk(plan):
    node = os.stat(plan["disk"])
    if (not stat.S_ISBLK(node.st_mode)
            or f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}" != plan["disk_id"]
            or int((Path("/sys/class/block") / Path(plan["disk"]).name / "size").read_text()) * 512
            != plan["disk_bytes"]):
        raise RuntimeError("The system disk identity changed")


def write_private(path, content):
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def operation_lock():
    descriptor = os.open(LOCK,
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as stream:
        if os.fstat(stream.fileno()).st_uid != 0:
            raise RuntimeError("Unsafe recovery operation lock")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another recovery preparation is running; wait and scan again") from error
        yield


def changed_table(before, plan, dump):
    after = copy.deepcopy(before)
    selected = next(p for p in after["partitions"] if p["node"] == plan["swap"])
    selected["size"] = plan["remaining_bytes"] // plan["sector_bytes"]
    new = {"node": plan["new_device"], "start": plan["new_start"],
           "size": RECOVERY_BYTES // plan["sector_bytes"], "type": LINUX_TYPE,
           "uuid": str(uuid4()), "name": "AnduinOS Recovery"}
    after["partitions"].append(new)
    after["partitions"].sort(key=lambda part: partition_number(plan["disk"], part["node"]))
    lines = dump.splitlines()
    matches = 0
    for index, line in enumerate(lines):
        if line.startswith(plan["swap"] + " "):
            lines[index], count = re.subn(r"size=\s*\d+", f"size= {selected['size']}", line)
            matches += count
    if matches != 1:
        raise RuntimeError("Cannot preserve the existing partition table exactly")
    lines.append(f'{new["node"]} : start= {new["start"]}, size= {new["size"]}, '
                 f'type={new["type"]}, uuid={new["uuid"]}, name="AnduinOS Recovery"')
    return after, "\n".join(lines) + "\n"


def write_table(commands, disk, text, dry=False):
    arguments = ["sfdisk", "--lock=yes", "--no-reread", "--no-tell-kernel",
                 "--wipe=never", "--wipe-partitions=never"]
    if dry:
        arguments.append("--no-act")
    commands.run(*arguments, disk, data=text)


def backup_metadata(plan, table, dump):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE.is_symlink() or STATE.stat().st_uid != 0:
        raise RuntimeError("Unsafe recovery backup directory")
    if STATE.parent.is_symlink() or STATE.parent.stat().st_uid != 0:
        raise RuntimeError("Unsafe recovery backup parent directory")
    STATE.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix="backup-", dir=STATE))
    sector = table["sectorsize"]
    first_size = table["firstlba"] * sector
    last_offset = (table["lastlba"] + 1) * sector
    if not (0 < first_size <= MIB and 0 < plan["disk_bytes"] - last_offset <= MIB):
        raise RuntimeError("Unsupported GPT metadata size")
    with open(plan["disk"], "rb", buffering=0) as stream:
        first = os.pread(stream.fileno(), first_size, 0)
        last = os.pread(stream.fileno(), plan["disk_bytes"] - last_offset, last_offset)
    with open(plan["swap"], "rb", buffering=0) as stream:
        header = stream.read(65536)
    if len(first) != first_size or len(last) != plan["disk_bytes"] - last_offset or len(header) != 65536:
        raise RuntimeError("Incomplete partition metadata backup")
    files = (("gpt-first.bin", first), ("gpt-last.bin", last), ("swap-header.bin", header),
             ("table.sfdisk", dump.encode()))
    hashes = {}
    for name, data in files:
        write_private(backup / name, data)
        if (backup / name).read_bytes() != data:
            raise RuntimeError("Partition backup verification failed")
        hashes[name] = hashlib.sha256(data).hexdigest()
    write_private(backup / "hashes.json", json.dumps(hashes, sort_keys=True).encode())
    return backup, last_offset


def restore_activation(commands, plan):
    active = any(os.path.realpath(s["name"]) == plan["swap"] for s in active_swaps())
    if plan["active"] and not active:
        commands.run("swapon", f"--priority={plan['priority']}", plan["swap"])
    elif not plan["active"] and active:
        raise RuntimeError("Swap activation changed outside this operation")
    if plan["active"]:
        selected = next(s for s in active_swaps() if os.path.realpath(s["name"]) == plan["swap"])
        if int(selected["prio"]) != plan["priority"]:
            raise RuntimeError("The original swap priority could not be restored")


def rollback(commands, plan, before, after, backup, last_offset):
    for name, expected in json.loads((backup / "hashes.json").read_text()).items():
        if hashlib.sha256((backup / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("A saved partition backup is damaged; automatic restoration stopped")
    verify_disk(plan)
    other_parts = copy.deepcopy(before)
    other_parts["partitions"] = [p for p in before["partitions"] if p["node"] != plan["swap"]]
    verify_geometry(other_parts)
    mounted = commands.run("findmnt", "--noheadings", "--source", plan["new_device"], check=False)
    if mounted.returncode not in (0, 1):
        raise RuntimeError("Cannot determine whether the new partition is mounted")
    if mounted.stdout.strip():
        raise RuntimeError("The new partition is mounted; unmount it before restoring the saved layout")
    if any(os.path.realpath(s["name"]) == plan["swap"] for s in active_swaps()):
        commands.run("swapoff", plan["swap"])
    if geometry(plan["new_device"]) is not None:
        new = next(p for p in after["partitions"] if p["node"] == plan["new_device"])
        if geometry(plan["new_device"]) != {"start": new["start"] * before["sectorsize"], "size": RECOVERY_BYTES}:
            raise RuntimeError("New partition identity changed; automatic rollback stopped")
        commands.run("partx", "--delete", "--nr", str(plan["new_number"]), plan["disk"])
    # Removing a kernel partition emits udev events whose probes may briefly
    # hold the disk lock. Finish them before restoring the saved GPT.
    commands.run("udevadm", "settle", "--timeout=15")
    with open(plan["disk"], "r+b", buffering=0) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, offset in (("gpt-first.bin", 0), ("gpt-last.bin", last_offset)):
            data = (backup / name).read_bytes()
            if os.pwrite(stream.fileno(), data, offset) != len(data):
                raise RuntimeError("Incomplete partition table restoration")
        os.fsync(stream.fileno())
    commands.run("partx", "--update", "--nr", str(plan["swap_number"]), plan["disk"])
    commands.run("udevadm", "settle", "--timeout=15")
    verify_geometry(before)
    if canonical(read_table(commands, plan["disk"])) != canonical(before):
        raise RuntimeError("The restored partition table could not be verified")
    with open(plan["swap"], "r+b", buffering=0) as stream:
        data = (backup / "swap-header.bin").read_bytes()
        if stream.write(data) != len(data):
            raise RuntimeError("Incomplete swap header restoration")
        os.fsync(stream.fileno())
    restore_activation(commands, plan)


def prepare(approved, *, commands=None, inspector=None):
    """root/runner substitutions are internal test seams, never helper arguments."""
    commands = commands or Commands(verbose=True)
    inspector = inspector or inspect_partition
    with operation_lock():
        inspection = inspector()
        if inspection.get("reason") != "ready" or inspection.get("plan") != approved:
            raise RuntimeError("The recovery preparation plan changed. Scan again; no partitions were changed.")
        plan, before = inspection["plan"], inspection["table"]
        verify_disk(plan)
        verify_geometry(before)
        dump = commands.text("sfdisk", "--dump", plan["disk"])
        after, text = changed_table(before, plan, dump)
        if table_hash(read_table(commands, plan["disk"])) != plan["table_hash"]:
            raise RuntimeError("The partition table changed. Scan again.")
        write_table(commands, plan["disk"], text, dry=True)
        backup, last_offset = backup_metadata(plan, before, dump)
        write_private(STATE / "pending.json", json.dumps({"plan": plan, "before": before,
            "after": after, "backup": str(backup), "last_offset": last_offset}, indent=2).encode())
        changed = False
        try:
            if plan["active"]:
                commands.run("swapoff", plan["swap"])
            commands.run("udevadm", "settle", "--timeout=15")
            verify_disk(plan)
            verify_geometry(before)
            if (table_hash(read_table(commands, plan["disk"])) != plan["table_hash"]
                    or identity(commands, plan["swap"])["UUID"] != plan["swap_uuid"]
                    or any(os.path.realpath(s["name"]) == plan["swap"] for s in active_swaps())):
                raise RuntimeError("The disk or swap state changed before writing")
            changed = True  # A failed sfdisk write can have changed part of the GPT.
            write_table(commands, plan["disk"], text)
            if canonical(read_table(commands, plan["disk"])) != canonical(after):
                raise RuntimeError("The new partition table does not match the approved layout")
            commands.run("partx", "--update", "--nr", str(plan["swap_number"]), plan["disk"])
            commands.run("udevadm", "settle", "--timeout=15")
            if geometry(plan["new_device"]) is None:
                # udev may already have registered it. Never format on partx's
                # exit code alone: inspect every kernel device's exact geometry.
                commands.run("partx", "--add", "--nr", str(plan["new_number"]), plan["disk"], check=False)
            commands.run("udevadm", "settle", "--timeout=15")
            verify_geometry(after)
            commands.run("mkswap", "--lock=yes", f"--uuid={plan['swap_uuid']}",
                         f"--label={plan['swap_label']}", plan["swap"])
            verify_disk(plan)
            verify_geometry(after)
            commands.run("mkfs.ext4", "-F", "-b", "4096", "-L", "AOS_RECOVERY", "-E",
                         "nodiscard,lazy_itable_init=0,lazy_journal_init=0", plan["new_device"])
            commands.run("e2fsck", "-f", "-n", plan["new_device"])
            info = identity(commands, plan["new_device"])
            swap = identity(commands, plan["swap"])
            if (info.get("TYPE") != "ext4" or info.get("LABEL") != "AOS_RECOVERY"
                    or swap.get("UUID") != plan["swap_uuid"] or swap.get("LABEL", "") != plan["swap_label"]):
                raise RuntimeError("The prepared filesystems could not be verified")
            verify_geometry(after)
            restore_activation(commands, plan)
        except Exception as error:
            try:
                if changed:
                    # A lock timeout may precede any disk write. If both table
                    # and header are unchanged, do not rewrite a healthy GPT.
                    try:
                        unchanged = canonical(read_table(commands, plan["disk"])) == canonical(before)
                    except (RuntimeError, ValueError, KeyError, subprocess.SubprocessError):
                        unchanged = False
                    if unchanged:
                        with open(plan["swap"], "rb") as stream:
                            unchanged = stream.read(65536) == (backup / "swap-header.bin").read_bytes()
                    if unchanged:
                        verify_geometry(before)
                        restore_activation(commands, plan)
                    else:
                        rollback(commands, plan, before, after, backup, last_offset)
                else:
                    restore_activation(commands, plan)
            except Exception as recovery_error:
                raise RuntimeError(f"Preparation failed: {error}\nAutomatic restoration stopped: {recovery_error}\n"
                                   f"Backups: {backup}\nDo not retry partition preparation. Ask an administrator "
                                   "to inspect the saved partition table and swap header first.") from error
            (STATE / "pending.json").unlink()
            raise RuntimeError(f"Preparation failed; the original layout and swap state were restored.\n"
                               f"{error}\nBackups: {backup}\nScan again before retrying.") from error
        (STATE / "pending.json").unlink()
        print(f"Recovery partition prepared: {plan['new_device']} (256 MiB, ext4, AOS_RECOVERY).", flush=True)
