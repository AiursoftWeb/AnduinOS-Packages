"""Local APT planning against copied system state. No install/commit API is used."""
import hashlib
import os
import pwd
import re
import shutil
import tempfile
from pathlib import Path

import apt
import apt_pkg
from apt.progress.base import AcquireProgress, OpProgress

from . import VERSION
from .manifest import identity, require
from .native import BundleError, control_text, paragraphs, run


def os_release(file):
    result = {}
    for line in Path(file).read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k] = v.strip().strip('\"\'')
    return result


def snapshot_plan(system_root):
    if Path(system_root).resolve() != Path("/"):
        return {"state": "unknown", "reason": "Captured state cannot prove a live snapshot layout"}
    policy = Path("/etc/anduinos-btrfs-snapshots-manager/apt-snapshots.toml")
    hook = Path("/usr/libexec/anduinos-btrfs-snapshots-manager-apt-hook")
    if not hook.is_file():
        return {"state": "unavailable", "reason": "No snapshot integration installed"}
    try:
        import tomllib
        if policy.is_file():
            data = tomllib.loads(policy.read_text())
            require(set(data) == {"apt"} and isinstance(data["apt"], dict), "Invalid snapshot policy")
            settings = data["apt"]
            require(set(settings) <= {"snapshot_before", "snapshot_after"} and
                    all(type(value) is bool for value in settings.values()), "Invalid snapshot policy")
            if settings.get("snapshot_before", True) is False:
                return {"state": "disabled", "reason": "Disabled in existing APT snapshot policy"}
        fs = run(["findmnt", "--noheadings", "--output", "FSTYPE", "--target", "/"]).decode().strip()
        if fs != "btrfs":
            return {"state": "unavailable", "reason": "System filesystem does not support this snapshot integration"}
    except (OSError, ValueError):
        return {"state": "unknown", "reason": "Snapshot policy/layout could not be read"}
    return {"state": "requires-layout-check", "reason": "APT policy requests a snapshot; complete recovery layout must be checked before installation"}


def installed(records):
    result = {}
    for record in records:
        if "Status" not in record:
            continue
        state = record["Status"].split()
        require(len(state) == 3 and state[1] == "ok" and state[2] in ("installed", "config-files", "not-installed"), "An unfinished dpkg transaction requires attention")
        if state[2] == "installed":
            require(all(record.get(field) for field in ("Package", "Architecture", "Version")), "Incomplete installed package record")
            key = (record["Package"], record["Architecture"])
            require(key not in result, "Duplicate installed package")
            result[key] = record
    return result


def configure(work, repository, keyring, system_root, architecture):
    # Read-only simulation deliberately excludes hooks; only package policy is copied.
    for name in ("etc/parts", "etc/sources.list.d", "etc/preferences.d", "state/lists/partial", "cache/archives/partial", "log"):
        (work / name).mkdir(parents=True, exist_ok=True)
    (work / "etc/empty.conf").write_text("")
    source = system_root / "etc/apt"
    for item in [source / "preferences", *sorted((source / "preferences.d").glob("*"))]:
        if item.is_file():
            require(item.stat().st_size <= 1024 ** 2, "APT preferences file is too large")
            dest = work / "etc" / item.relative_to(source)
            dest.write_bytes(item.read_bytes())
    (work / "trusted.gpg").write_bytes(Path(keyring).read_bytes())
    (work / "etc/sources.sources").write_text(
        f'Types: deb\nURIs: {repository.resolve().as_uri()}\nSuites: ./\nArchitectures: {architecture}\nSigned-By: {work / "trusted.gpg"}\n')
    for name in {key.split("::", 1)[0] for key in apt_pkg.config.keys()}:
        apt_pkg.config.clear(name)
    # Set Dir::Etc before initialization: never load caller-supplied APT_CONFIG/hooks.
    apt_pkg.config.set("Dir::Etc", str(work / "etc"))
    apt_pkg.config.set("Dir::Etc::parts", "parts")
    apt_pkg.config.set("Dir::Etc::main", "empty.conf")
    original_config = os.environ.pop("APT_CONFIG", None)
    try:
        apt_pkg.init_config()
    finally:
        if original_config is not None:
            os.environ["APT_CONFIG"] = original_config
    values = {"Dir": str(work), "Dir::Etc": str(work / "etc"),
              "Dir::Etc::sourcelist": "sources.sources", "Dir::Etc::sourceparts": "sources.list.d",
              "Dir::Etc::preferences": "preferences", "Dir::Etc::preferencesparts": "preferences.d",
              "Dir::State": str(work / "state"), "Dir::State::status": str(work / "state/status"),
              "Dir::Cache": str(work / "cache"), "Dir::Cache::pkgcache": "", "Dir::Cache::srcpkgcache": "",
              "Dir::Log": str(work / "log"), "APT::Architecture": architecture,
              "APT::Sandbox::User": pwd.getpwuid(os.getuid()).pw_name,
              "Acquire::Languages": "none", "Acquire::Retries": "0",
              "Acquire::AllowInsecureRepositories": "false", "APT::Get::AllowUnauthenticated": "false"}
    for key, value in values.items():
        apt_pkg.config.set(key, value)
    apt_pkg.config.clear("APT::Architectures")
    apt_pkg.config.set("APT::Architectures::", architecture)
    # Preserve solver choices, not arbitrary hooks. apt-config only reads configuration.
    if system_root == Path("/"):
        dump = run(["apt-config", "dump"]).decode()
        for key in ("APT::Default-Release", "APT::Install-Recommends", "APT::Install-Suggests"):
            match = re.search(r'^' + re.escape(key) + r' "([^"\n]*)";$', dump, re.M)
            if match:
                apt_pkg.config.set(key, match[1])
    extended = system_root / "var/lib/apt/extended_states"
    if extended.is_file():
        shutil.copyfile(extended, work / "state/extended_states")
    # Defense in depth: importing python-apt may have loaded host hooks before us.
    for hook in ("APT::Update", "DPkg::Pre-Invoke", "DPkg::Post-Invoke", "DPkg::Pre-Install-Pkgs"):
        apt_pkg.config.clear(hook)
    apt_pkg.init_system()


def resolve_stage(cache, stage, m, *, bootstrap_needed=False):
    """One solver and safety gate for both simulation and installation."""
    catalog = {identity(p["identity"]): p for p in m["packages"]}
    require(cache.broken_count == 0, "The current package dependencies are broken")
    if stage["operation"] == "dist-upgrade":
        cache.upgrade(dist_upgrade=True)
    else:
        for name in stage["packages"]:
            require(name in cache and cache[name].candidate is not None, f"No local candidate for {name}")
            cache[name].mark_install(auto_fix=True, auto_inst=True, from_user=False)
    require(cache.broken_count == 0, "Local bundle cannot resolve required dependencies")
    changes, replacements, peak_installed = [], [], 0
    for item in cache.get_changes():
        before = item.installed.version if item.installed else None
        arch = item.installed.architecture if item.marked_delete else item.candidate.architecture
        require(item._pkg.selected_state != apt_pkg.SELSTATE_HOLD, f"Held package would change: {item.name}")
        if item.marked_delete:
            require(not bootstrap_needed and not item.essential and item.name in m["allowed_removals"], f"Unapproved package removal: {item.name}")
            after = None
        else:
            candidate = item.candidate
            after = candidate.version
            require(before is None or apt_pkg.version_compare(after, before) >= 0, f"Downgrade refused: {item.name}")
            pkg = catalog.get((item.name, after, arch))
            require(pkg is not None and any(o.trusted for o in candidate.origins), f"Candidate is outside the authenticated bundle: {item.name}")
            record = paragraphs(str(candidate.record))[0]
            require(record.get("SHA256") == pkg["content"]["sha256"] and record.get("Filename") == pkg["path"][len("repository/"):], f"Candidate identity differs from bundle: {item.name}")
            peak_installed += max(0, candidate.installed_size)
            for key in ("Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512", "Description-md5"):
                record.pop(key, None)
            record["Status"] = "install ok installed"
            replacements.append(record)
        changes.append({"action": "remove" if after is None else "install", "name": item.name,
                        "architecture": arch, "before": before, "after": after})
    return sorted(changes, key=lambda c: (c["name"], c["architecture"])), replacements, peak_installed


def simulate(m, repository, keyring, system_root, work, *, client_version=VERSION):
    status_file = system_root / "var/lib/dpkg/status"
    status_bytes = status_file.read_bytes()
    records = paragraphs(status_bytes.decode())
    initial = installed(records)
    require(all(arch in (m["architecture"], "all") for _, arch in initial), "Foreign-architecture installed packages are not supported by this preview")
    configure(work, repository, keyring, system_root, m["architecture"])
    status = work / "state/status"
    status.write_bytes(status_bytes)
    catalog = {identity(p["identity"]): p for p in m["packages"]}
    target_by_key = {(name, arch): ver for name, ver, arch in catalog}
    with apt.Cache(progress=OpProgress(), memonly=True) as cache:
        cache.update(AcquireProgress(), pulse_interval=0)
    boot = m.get("bootstrap")
    bootstrap_needed = boot is not None and apt_pkg.version_compare(client_version, boot["minimum_version"]) < 0
    extra = m.get("required_installer_features", [])
    if extra and not bootstrap_needed:
        raise BundleError("This updater does not implement: " + ", ".join(extra) + "; a compatible updater is required")
    stages = [{"operation": "install", "packages": [boot["package"]]}] if bootstrap_needed else m["stages"]
    results, peak_installed = [], 0
    for stage in stages:
        with apt.Cache(progress=OpProgress(), memonly=True) as cache:
            changes, replacements, expanded = resolve_stage(cache, stage, m, bootstrap_needed=bootstrap_needed)
            peak_installed += expanded
            changed = {(c["name"], c["architecture"]) for c in changes}
            records = [r for r in records if (r.get("Package"), r.get("Architecture")) not in changed] + replacements
            status.write_text(control_text(records))
            results.append({"stage": stage, "changes": sorted(changes, key=lambda c: (c["name"], c["architecture"]))})
    final = installed(records)
    if bootstrap_needed:
        updated = [r for (name, _), r in final.items() if name == boot["package"]]
        require(len(updated) == 1 and apt_pkg.version_compare(updated[0]["Version"], boot["minimum_version"]) >= 0,
                "Bootstrap updater is held back or unavailable")
        require(any(c["name"] == boot["package"] for s in results for c in s["changes"]), "Installed updater and running version disagree; restart the updater")
    else:
        for key, minimum in target_by_key.items():
            if key not in initial and key not in final:
                continue
            if key not in final and key[0] in m["allowed_removals"]:
                continue
            require(key in final and apt_pkg.version_compare(final[key]["Version"], minimum) >= 0,
                    f"Required installed target not satisfied: {key[0]} >= {minimum}")
        require(any(name == m["version_package"] for name, _ in final), "OS version package is missing")
    require(status_file.read_bytes() == status_bytes, "System package state changed during preview; try again")
    return results, peak_installed, bootstrap_needed, hashlib.sha256(status_bytes).hexdigest()


def preview(m, repository, keyring, system_root, workspace_root=None, *, client_version=VERSION):
    system_root = Path(system_root).resolve()
    os_info = os_release(system_root / "usr/lib/os-release")
    require(os_info.get("ID") == "anduinos" and os_info.get("UBUNTU_CODENAME") == m["suite"] and
            os_info.get("VERSION_ID", "").startswith(m["release_line"] + "."), "System is outside this bundle's release line or suite")
    current = os_info["VERSION_ID"]
    require(apt_pkg.version_compare(current, m["target_version"]) <= 0, "Older target OS version; downgrade refused")
    if system_root == Path("/"):
        require(run(["dpkg", "--print-architecture"]).decode().strip() == m["architecture"], "Bundle architecture does not match this machine")
    result = {"current_version": current, "target_version": m["target_version"], "batch_id": m["batch_id"],
              "state": "blocked", "read_only": True, "can_install": False, "stages": [], "blockers": [],
              "snapshot": snapshot_plan(system_root), "space": {"staging_bytes": sum(f["size"] for f in m["files"].values()),
              "snapshot_bytes": None, "peak_install_bytes": None, "requires_installation_recheck": True,
              "measurement_scope": "live-system" if system_root == Path("/") else "validation-host"}}
    try:
        with tempfile.TemporaryDirectory(prefix="offline-plan-", dir=workspace_root) as name:
            stages, expanded, bootstrap, status_hash = simulate(m, repository, keyring, system_root, Path(name), client_version=client_version)
            result.update(stages=stages, status_sha256=status_hash)
            result["space"].update(package_expansion_upper_estimate_bytes=expanded,
                                   observed_available_bytes=shutil.disk_usage(system_root).free)
            require(shutil.disk_usage(system_root).free >= expanded, "Insufficient system space for the conservative package expansion estimate")
            changed = any(s["changes"] for s in stages)
            require(changed or current == m["target_version"], "OS version differs despite an empty APT plan")
            result["state"] = "updater-update-required" if bootstrap else "update-available" if changed else "already-installed"
    except (BundleError, apt_pkg.Error, SystemError, OSError) as error:
        result["blockers"].append(str(error))
    return result
