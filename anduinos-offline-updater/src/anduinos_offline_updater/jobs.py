"""Boot-scoped staging for owner-confirmed offline updates."""
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
CLEANUP_HELPER = "/usr/libexec/anduinos-offline-updater-cleanup"
JOB_NAME = re.compile(r"[a-f0-9]{32}")
PREVIEW_NAME = re.compile(r"offline-updater-[a-z0-9_]{8}")
TERMINAL = {"complete", "already-installed", "blocked", "partial", "failed", "interrupted"}


def atomic_json(path, value, mode=0o600):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
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


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def needs_cleanup(directory):
    if not directory.exists():
        return False
    try:
        return json.loads((directory / ".boot-id").read_text()) != boot_id()
    except (FileNotFoundError, ValueError):
        return True


def clean_previous_boot(directory, names, owner):
    """Remove only this application's staging directories, once on first use."""
    if not needs_cleanup(directory):
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        require(meta.st_uid == owner and not meta.st_mode & 0o022, "Invalid updater storage directory")
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not needs_cleanup(directory):
            return
        # On the first upgrade there is no marker. Old installations and
        # previews created during this boot may still be in use.
        legacy = not (directory / ".boot-id").exists()
        started = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                       if line.startswith("btime "))
        for path in directory.iterdir():
            if not names.fullmatch(path.name):
                continue
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and info.st_uid == owner and (not legacy or info.st_mtime < started):
                shutil.rmtree(path)
        atomic_json(directory / ".boot-id", boot_id(), mode=0o644)
    finally:
        os.close(fd)


def job_path(identifier):
    require(JOB_NAME.fullmatch(identifier) is not None, "Invalid job identifier")
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
    if report.get("boot_id"):
        require(report["boot_id"] == boot_id(), "This update belongs to a previous boot")
    plan = job_path(identifier) / "plan.json"
    if report["state"] == "prepared" and (report.get("confirmation_required") or
            (plan.exists() and plan.stat().st_mode & stat.S_IROTH)):
        # New jobs expose package metadata for reconnecting GTK. Legacy private
        # plans remain queryable without requiring their contents to be readable.
        report["preview"] = json.loads((job_path(identifier) / "plan.json").read_text())
    if not report.get("source_version") or not report.get("target_version"):
        # Older jobs kept versions only in the public installation plan.
        # Never infer a completed job's starting version from today's system.
        try:
            if plan.stat().st_mode & stat.S_IROTH:
                preview = json.loads(plan.read_text())
                for name, key in (("source_version", "current_version"), ("target_version", "target_version")):
                    value = preview.get(key)
                    if isinstance(value, str) and value:
                        report.setdefault(name, value)
        except (OSError, ValueError, AttributeError):
            pass
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
        with os.fdopen(read_fd, "rb") as incoming, open(destination, "xb",
                opener=lambda path, flags: os.open(path, flags, 0o600)) as outgoing:
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


def inspect_import(path, *, allow_foreign_removals=False):
    # Separate interpreter: read-only APT configuration must not leak into live APT.
    arguments = ["/usr/bin/anduinos-offline-updater", "inspect", str(path / "bundle.atgz"),
                 "--work-directory", str(path), "--json"]
    if allow_foreign_removals:
        arguments.append("--allow-foreign-removals")
    result = subprocess.run(arguments,
                            capture_output=True, text=True, check=False)
    require(result.returncode in (0, 2), "Imported update could not be previewed")
    return json.loads(result.stdout)


def ensure_installable(report):
    if report["state"] == "foreign-removal-required":
        packages = ", ".join(p["name"] + ":" + p["architecture"] for p in report["foreign_removals"])
        raise BundleError("Removing these packages requires explicit consent: " + packages +
                         ". Review with the desktop app or use --allow-foreign-removals.")
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


def prepare(source, uid, *, allow_foreign_removals=False):
    trusted_directory(ROOT, create=True)
    clean_previous_boot(ROOT, JOB_NAME, 0)
    path = ROOT / uuid.uuid4().hex; path.mkdir(mode=0o711)
    try:
        update(path, schema_version=1, job_id=path.name, owner_uid=uid, boot_id=boot_id(), state="importing")
        digest, size = copy_as_user(source, path / "bundle.atgz", uid)
        report = inspect_import(path, allow_foreign_removals=True) if allow_foreign_removals else inspect_import(path)
        ensure_installable(report)
        # Bind confirmation to the complete root-produced plan, not caller JSON.
        plan_hash = save_plan(path, report)
        # Keep the large package plan out of frequently rewritten progress records.
        result = update(path, state="prepared", archive_sha256=digest, archive_bytes=size,
                        plan_sha256=plan_hash, allow_foreign_removals=allow_foreign_removals,
                        source_version=report["current_version"],
                        target_version=report["target_version"])
        return dict(result, preview=report)
    except (Exception, KeyboardInterrupt):
        shutil.rmtree(path)
        raise


def start(identifier, plan_hash, uid):
    clean_previous_boot(ROOT, JOB_NAME, 0)
    path = job_path(identifier)
    with job_lock(path):
        report = status(identifier, uid)
        require(report["state"] == "prepared" and report["plan_sha256"] == plan_hash,
                "The confirmed plan is no longer available")
        update(path, state="queued", confirmation_required=False, boot_id=boot_id())
    try:
        # The caller waits for this child. The inhibitor lasts only while the
        # approved package transaction is running.
        result = subprocess.run(["/usr/bin/systemd-inhibit", "--what=shutdown:sleep:idle", "--mode=block",
                                 "--who=AnduinOS Offline Updater", "--why=Installing a verified offline update",
                                 HELPER, "run", identifier], stdout=subprocess.DEVNULL, check=False)
    except OSError as error:
        update(path, state="blocked", error=str(error))
    else:
        if result.returncode and status(identifier, uid)["state"] not in TERMINAL:
            update(path, state="interrupted", error="Installation interrupted. Check the package state before retrying.")
    return status(identifier, uid)


def discard(identifier, uid):
    path = job_path(identifier)
    with job_lock(path):
        report = status(identifier, uid)
        require(report["state"] == "prepared" or report["state"] in TERMINAL,
                "An active installation cannot be discarded")
        shutil.rmtree(path)
    return {"job_id": identifier, "state": "discarded"}
