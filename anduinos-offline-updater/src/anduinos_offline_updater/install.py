"""Native APT installation; called only by the fixed root systemd worker."""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import apt
import apt_pkg
from apt.progress.base import AcquireProgress, InstallProgress, OpProgress

from . import jobs
from .bundle import verify
from .manifest import require
from .native import paragraphs, run
from .planning import installed, os_release, resolve_stage

SNAPSHOTS = Path("/.snapshots/anduinos-btrfs-snapshots-manager")


def configure(path, repository, architecture):
    # Load normal host policy and dpkg hooks. Only this process's sources and
    # download/index storage change; the real dpkg and auto-installed state stay.
    os.environ.pop("APT_CONFIG", None)
    for name in {key.split("::", 1)[0] for key in apt_pkg.config.keys()}:
        apt_pkg.config.clear(name)
    apt_pkg.init_config()
    for name in ("lists/partial", "archives/partial", "empty-sources"):
        (path / name).mkdir(parents=True, exist_ok=True)
    (path / "bundle.sources").write_text(
        f"Types: deb\nURIs: {repository.as_uri()}\nSuites: ./\nArchitectures: {architecture}\nSigned-By: {jobs.KEYRING}\n")
    values = {"Dir": "/", "Dir::State::status": "/var/lib/dpkg/status",
              "Dir::State::extended_states": "/var/lib/apt/extended_states",
              "Dir::Etc::sourcelist": str(path / "bundle.sources"),
              "Dir::Etc::sourceparts": str(path / "empty-sources"),
              "Dir::State::lists": str(path / "lists"), "Dir::Cache::archives": str(path / "archives"),
              "Dir::Cache::pkgcache": "", "Dir::Cache::srcpkgcache": "",
              "Acquire::Languages": "none", "Acquire::Retries": "0", "APT::Sandbox::User": "_apt",
              "Acquire::AllowInsecureRepositories": "false", "APT::Get::AllowUnauthenticated": "false"}
    for key, value in values.items(): apt_pkg.config.set(key, value)
    # Index hooks can refresh unrelated online application catalogs. Package
    # hooks (including AnduinOS snapshots and boot integration) remain intact.
    apt_pkg.config.clear("APT::Update")
    apt_pkg.config.set("DPkg::Options::", "--force-confdef")
    apt_pkg.config.set("DPkg::Options::", "--force-confold")
    apt_pkg.init_system()


class DownloadProgress(AcquireProgress):
    def __init__(self, path):
        super().__init__(); self.path = path

    def pulse(self, owner):
        jobs.update(self.path, progress={"phase": "local-copy", "bytes": self.current_bytes, "total_bytes": self.total_bytes})
        return True


class PackageProgress(InstallProgress):
    def __init__(self, path):
        super().__init__(); self.path = path

    def status_change(self, package, percent, status):
        jobs.update(self.path, progress={"phase": "dpkg", "package": package, "percent": percent, "message": status})

    def error(self, package, message):
        jobs.update(self.path, package_error={"package": package, "message": message})


def space_check(path, manifest, plan):
    # Conservative additional capacity, measured after the staged payload exists.
    # Maintainer-script/initramfs/CoW peaks are estimates, never exact promises.
    expansion = plan["space"]["package_expansion_upper_estimate_bytes"]
    required = {"/": expansion + 512 * 1024 ** 2,
                str(path): sum(p["content"]["size"] for p in manifest["packages"])}
    for mount, reserve in (("/boot", 256 * 1024 ** 2), ("/boot/efi", 64 * 1024 ** 2)):
        if os.path.ismount(mount): required[mount] = reserve
    devices = {}
    for location, needed in required.items():
        device = os.stat(location).st_dev
        entry = devices.setdefault(device, {"path": location, "required_bytes": 0})
        entry["required_bytes"] += needed
    result = []
    for entry in devices.values():
        entry["available_bytes"] = shutil.disk_usage(entry["path"]).free
        result.append(entry)
        require(entry["available_bytes"] >= entry["required_bytes"],
                f"Insufficient free space on {entry['path']}; additional capacity estimate: {entry['required_bytes']} bytes")
    return result


def snapshot_records():
    directory = SNAPSHOTS / "transactions/package-history"
    return set(directory.glob("*.json")) if directory.is_dir() else set()


def snapshot_result(before, plan, expected=1):
    result = {"state": "unconfirmed", "transactions": []}
    if expected == 0:
        return {"state": "not-needed", "transactions": []}
    if plan["state"] in ("unavailable", "disabled"):
        result["state"] = plan["state"]
    try:
        for file in sorted(snapshot_records() - before):
            record = json.loads(file.read_text())
            identifier = record.get("pre_deployment_id")
            verified = False
            if identifier:
                # The recovery store validates metadata and the actual subvolume.
                output = json.loads(run(["/usr/bin/anduinos-btrfs-snapshots-manager-cli", "list", "--json"]))
                require(isinstance(output, list), "Unexpected snapshot inventory format")
                for deployment in output:
                    if deployment.get("id") != identifier or deployment.get("state") != "ready": continue
                    root = SNAPSHOTS / "deployments" / identifier / "root"
                    details = run(["btrfs", "subvolume", "show", str(root)]).decode()
                    match = re.search(r"^\s*UUID:\s+(\S+)$", details, re.M)
                    verified = match is not None and match[1] == deployment.get("snapshot_uuid")
            result["transactions"].append({"id": record["id"], "pre_deployment_id": identifier,
                                           "pre_snapshot_verified": verified, "failure": record.get("failure")})
        if plan["state"] not in ("unavailable", "disabled"):
            result["state"] = "created" if len(result["transactions"]) >= expected and all(
                t["pre_snapshot_verified"] and not t["failure"] for t in result["transactions"]) else "unconfirmed"
    except Exception as error:
        # Snapshot reporting is best effort, like the existing APT hook. Its
        # failure must not erase a successfully verified package result.
        result["state"] = "unconfirmed"
        result["reason"] = str(error)[-1000:]
    return result


def check_targets(manifest, initial, final):
    for package in manifest["required_installed_targets"]:
        key = (package["name"], package["architecture"])
        if key not in initial and key not in final: continue
        if key not in final and key[0] in manifest["allowed_removals"]: continue
        require(key in final and apt_pkg.version_compare(final[key]["Version"], package["version"]) >= 0,
                f"Required installed target not satisfied: {key[0]} >= {package['version']}")
    require(any(name == manifest["version_package"] for name, _ in final), "OS version package is missing")


def execute(identifier):
    path = jobs.job_path(identifier)
    with jobs.job_lock(path):
        job = jobs.status(identifier, 0)
        require(job["state"] == "queued", "Job is not queued")
        wrote_packages, completed, attempted = False, 0, 0
        snapshot_before = snapshot_records()
        plan = json.loads((path / "plan.json").read_text())
        try:
            jobs.update(path, state="checking")
            with (path / "bundle.atgz").open("rb") as stream:
                require(hashlib.file_digest(stream, "sha256").hexdigest() == job["archive_sha256"], "Imported file identity changed")
            verified = verify(path / "bundle.atgz", jobs.KEYRING, path / "payload")
            manifest = verified["manifest"]
            current = jobs.inspect_import(path)
            jobs.ensure_installable(current)
            require(current["stages"] == plan["stages"] and current["current_version"] == plan["current_version"],
                    "Installation plan changed; inspect and confirm again")
            repository = path / "payload/repository"
            configure(path, repository, manifest["architecture"])
            with apt_pkg.SystemLock():
                raw = Path("/var/lib/dpkg/status").read_bytes()
                require(hashlib.sha256(raw).hexdigest() == current["status_sha256"], "Package state changed; inspect and confirm again")
                initial = installed(paragraphs(raw.decode()))
                require(not run(["dpkg", "--audit"]).strip(), "dpkg reports unfinished work; repair it before updating")
                with apt.Cache(progress=OpProgress(), memonly=True) as cache:
                    cache.update(AcquireProgress(), pulse_interval=0)
                if any(s["changes"] for s in plan["stages"]):
                    jobs.update(path, space=space_check(path, manifest, current))
                for number, approved in enumerate(plan["stages"], start=1):
                    with apt.Cache(progress=OpProgress(), memonly=True) as cache:
                        changes, _, _ = resolve_stage(cache, approved["stage"], manifest)
                        require(changes == approved["changes"], "APT plan changed; no further stages will run")
                        if changes:
                            space_check(path, manifest, current)
                            # Both before and after approval we disallow self replacement until
                            # the separate bootstrap/process-handoff milestone is implemented.
                            jobs.ensure_installable({"state": "update-available", "stages": [{"changes": changes}]})
                            progress = PackageProgress(path)
                            jobs.update(path, state="installing", stage=number, stage_count=len(plan["stages"]))
                            wrote_packages = True
                            attempted += 1
                            try:
                                cache.commit(fetch_progress=DownloadProgress(path), install_progress=progress,
                                             allow_unauthenticated=False)
                            finally:
                                progress.write_stream.close(); progress.status_stream.close()
                            cache.open(OpProgress())
                            require(cache.broken_count == 0, "Installed package dependencies are broken")
                            actual = installed(paragraphs(Path("/var/lib/dpkg/status").read_text()))
                            for change in changes:
                                record = actual.get((change["name"], change["architecture"]))
                                require((record["Version"] if record else None) == change["after"],
                                        "Installed version differs from plan: " + change["name"])
                        completed = number
                        jobs.update(path, completed_stages=completed)
                final = installed(paragraphs(Path("/var/lib/dpkg/status").read_text()))
                check_targets(manifest, initial, final)
                require(os_release("/usr/lib/os-release").get("VERSION_ID") == manifest["target_version"], "Target OS version was not installed")
                require(not run(["dpkg", "--audit"]).strip(), "dpkg reports unfinished work after installation")
            jobs.update(path, state="complete" if wrote_packages else "already-installed", progress=None,
                        reboot_required=Path("/var/run/reboot-required").exists(),
                        snapshot=snapshot_result(snapshot_before, plan["snapshot"], attempted))
        except Exception as error:
            jobs.update(path, state="partial" if wrote_packages else "blocked", error=str(error)[-4000:],
                        completed_stages=completed, snapshot=snapshot_result(snapshot_before, plan["snapshot"], attempted))
            return 2
        return 0
