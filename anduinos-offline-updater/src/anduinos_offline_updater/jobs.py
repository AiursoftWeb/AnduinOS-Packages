"""Root-owned imports and durable, owner-scoped installation jobs."""
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .manifest import MAX_PAYLOAD, require
from .native import BundleError, run

ROOT = Path("/var/lib/anduinos-offline-updater")
KEYRING = Path("/usr/share/keyrings/anduinos-offline-updates.gpg")
HELPER = "/usr/libexec/anduinos-offline-updater-helper"
TERMINAL = {"complete", "already-installed", "blocked", "partial", "failed", "interrupted"}


def atomic_json(path, value, mode=0o600):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def trusted_directory(path, create=False):
    if create:
        path.mkdir(mode=0o711, exist_ok=True)
    metadata = path.lstat()
    require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022,
            "Installation job directory must be a real root-owned directory")
    return path


def job_path(identifier):
    require(re.fullmatch(r"[a-f0-9]{32}", identifier) is not None, "Invalid job identifier")
    trusted_directory(ROOT)
    return trusted_directory(ROOT / identifier)


@contextmanager
def job_lock(path):
    # Serializes our job metadata only. Installation separately uses native APT locks.
    fd = os.open(path / "job.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error: raise BundleError("This job is busy") from error
        yield
    finally:
        os.close(fd)


def status(identifier, uid):
    report = json.loads((job_path(identifier) / "status.json").read_text())
    require(uid in (0, report["owner_uid"]), "This job belongs to another user")
    if report["state"] in {"queued", "checking", "installing", "handoff"} and time.time() - report["updated_at"] > 10:
        active = subprocess.run(["systemctl", "show", "--value", "--property=ActiveState", report["unit"] + ".service"],
                                capture_output=True, text=True, check=False, timeout=10)
        boot_changed = report.get("boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if boot_changed or active.returncode != 0 or active.stdout.strip() in {"inactive", "failed"}:
            report.update(state="interrupted", error="Installation service stopped without a final result; inspect dpkg before retrying")
    if report["state"] == "prepared" and report.get("confirmation_required"):
        # Older prepared jobs have a private plan and no handoff to review.
        report["preview"] = json.loads((job_path(identifier) / "plan.json").read_text())
    return report


def update(path, **fields):
    file = path / "status.json"
    data = json.loads(file.read_text()) if file.exists() else {}
    data.update(fields, updated_at=time.time())
    atomic_json(file, data, mode=0o644)
    return data


def copy_as_user(source, destination, uid):
    """The root process never opens the caller's path, even after authorization."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            account = pwd.getpwuid(uid)
            os.initgroups(account.pw_name, account.pw_gid)
            os.setgid(account.pw_gid); os.setuid(uid)
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_PAYLOAD,
                    "Select a regular, bounded update file")
            with os.fdopen(fd, "rb") as incoming, os.fdopen(write_fd, "wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write_fd)
    digest, size = hashlib.sha256(), 0
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(read_fd, "rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                size += len(chunk)
                require(size <= MAX_PAYLOAD, "Update file exceeds the import limit")
                require(shutil.disk_usage(destination.parent).free >= len(chunk) + 64 * 1024 ** 2,
                        "Insufficient space to import update file")
                outgoing.write(chunk); digest.update(chunk)
            outgoing.flush(); os.fsync(outgoing.fileno())
    finally:
        _, result = os.waitpid(pid, 0)
    require(result == 0 and size > 0, "Cannot read the selected regular file as the requesting user")
    return digest.hexdigest(), size


def inspect_import(path):
    # Separate interpreter: read-only APT configuration must not leak into live APT.
    result = subprocess.run(["/usr/bin/anduinos-offline-updater", "inspect", str(path / "bundle.atgz"),
                             "--work-directory", str(path), "--json"],
                            capture_output=True, text=True, check=False)
    require(result.returncode in (0, 2), "Imported update could not be previewed")
    return json.loads(result.stdout)


def ensure_installable(report):
    require(report["state"] in ("update-available", "already-installed", "updater-update-required"),
            "; ".join(report.get("blockers", [])) or "This bundle requires a newer installation engine")
    if report["state"] == "updater-update-required":
        require(len(report["stages"]) == 1 and report["stages"][0]["stage"]["operation"] == "install" and
                report["stages"][0]["changes"] and
                all(c["action"] == "install" for c in report["stages"][0]["changes"]),
                "Bootstrap must be one explicit installation without removals")
    else:
        from .planning import replaces_runtime
        require(not any(replaces_runtime(c["name"]) for s in report["stages"] for c in s["changes"]),
                "Updater/runtime changes require a separate process handoff")


def save_plan(path, report):
    plan_hash = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    # Like dpkg's public package inventory, the plan contains no archive data.
    # The unprivileged owner needs it to review a post-handoff confirmation.
    atomic_json(path / "plan.json", report, mode=0o644)
    return plan_hash


def prepare(source, uid):
    trusted_directory(ROOT, create=True)
    path = ROOT / uuid.uuid4().hex; path.mkdir(mode=0o711)
    try:
        update(path, schema_version=1, job_id=path.name, owner_uid=uid, state="importing")
        digest, size = copy_as_user(source, path / "bundle.atgz", uid)
        report = inspect_import(path)
        ensure_installable(report)
        # Bind confirmation to the complete root-produced plan, not caller JSON.
        plan_hash = save_plan(path, report)
        # Keep the large package plan out of frequently rewritten progress records.
        result = update(path, state="prepared", archive_sha256=digest, archive_bytes=size,
                        plan_sha256=plan_hash)
        return dict(result, preview=report)
    except Exception:
        shutil.rmtree(path)
        raise


def start(identifier, plan_hash, uid):
    path = job_path(identifier)
    with job_lock(path):
        report = status(identifier, uid)
        require(report["state"] == "prepared" and report["plan_sha256"] == plan_hash,
                "The confirmed plan is no longer available")
        unit = "anduinos-offline-update-" + identifier + "-" + uuid.uuid4().hex[:8]
        update(path, state="queued", confirmation_required=False, unit=unit, boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        try:
            run(["systemd-run", "--quiet", "--collect", "--unit=" + unit,
                 "--property=Type=exec", "--property=TimeoutStopSec=infinity",
                 "--property=StandardInput=null", "--property=UMask=0022",
                 "--property=Environment=DEBIAN_FRONTEND=noninteractive",
                 "--", "/usr/bin/systemd-inhibit", "--what=shutdown:sleep:idle", "--mode=block",
                 "--who=AnduinOS Offline Updater", "--why=Installing a verified offline update",
                 HELPER, "run", identifier])
        except Exception:
            update(path, state="blocked", error="Could not start installation service; no packages changed")
            raise
    return status(identifier, uid)


def discard(identifier, uid):
    path = job_path(identifier)
    with job_lock(path):
        report = status(identifier, uid)
        require(report["state"] == "prepared" or report["state"] in TERMINAL,
                "An active installation cannot be discarded")
        shutil.rmtree(path)
    return {"job_id": identifier, "state": "discarded"}
