"""Preview and commit one installed driver's exact APT transaction."""
from __future__ import annotations

import os
import re

import apt
import apt_pkg
from apt.progress.base import AcquireProgress, InstallProgress


class DownloadProgress(AcquireProgress):
    def __init__(self):
        super().__init__()
        self._last_percent = -1

    def pulse(self, owner):
        percent = int(100 * self.current_bytes / self.total_bytes) if self.total_bytes else 0
        if percent != self._last_percent:
            self._last_percent = percent
            print(f"Download: {percent}% ({int(self.current_bytes)} / {int(self.total_bytes)} bytes)", flush=True)
        return True

    def fail(self, item):
        print(f"Download failed: {item.description}", flush=True)


class PackageProgress(InstallProgress):
    def status_change(self, pkg, percent, status):
        print(f"Install: {percent:.0f}% · {pkg} · {status}", flush=True)

    def error(self, pkg, errormsg):
        print(f"Install failed: {pkg}: {errormsg}", flush=True)


def plan(cache, package: str, kernel: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package) or package not in cache:
        raise ValueError("Unknown driver package")
    selected = cache[package]
    if not selected.installed or not selected.candidate:
        raise ValueError("The selected driver is not installed or has no candidate")
    before, after = selected.installed.version, selected.candidate.version
    if apt_pkg.version_compare(after, before) <= 0:
        raise RuntimeError(f"No update is available for {package}: {before}")
    if cache.broken_count:
        raise RuntimeError("APT has broken dependencies; repair them before updating drivers")
    cache.clear()
    selected.mark_upgrade(from_user=False)
    if cache.broken_count:
        raise RuntimeError("APT could not resolve the driver update dependencies")
    changes = []
    for item in cache.get_changes():
        old = item.installed.version if item.installed else None
        new = None if item.marked_delete else item.candidate.version
        if item._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
            raise RuntimeError(f"The update would change a held package: {item.name}")
        if new is None:
            # Allow obsolete NVIDIA module/library replacements, not removal of
            # kernels, desktop metapackages or unrelated software.
            name = item.name.split(":", 1)[0]
            nvidia = re.fullmatch(
                r"(?:linux-modules-nvidia-[0-9]+(?:-open)?-.+|"
                r"libnvidia-[a-z0-9-]+|nvidia-[a-z0-9-]+|xserver-xorg-video-nvidia-[0-9]+)", name)
            if item.essential or not nvidia or name == package or kernel in name:
                raise RuntimeError(f"Unsafe package removal refused: {item.name}")
        elif old and apt_pkg.version_compare(new, old) < 0:
            raise RuntimeError(f"Package downgrade refused: {item.name}")
        if new is not None and not any(origin.trusted for origin in item.candidate.origins):
            raise RuntimeError(f"Untrusted package source: {item.name}")
        changes.append({"package": item.fullname, "before": old, "after": new})
    if not any(c["package"].split(":")[0] == package and c["after"] == after for c in changes):
        raise RuntimeError("APT did not schedule the requested driver update")
    return {"package": package, "before": before, "after": after,
            "changes": sorted(changes, key=lambda c: c["package"])}


def preview(package: str) -> dict:
    with apt.Cache() as cache:
        return plan(cache, package, os.uname().release)


def execute(package: str, approved: dict) -> dict:
    # Lock before reading dpkg state, and keep the lock through verification and
    # commit. libapt's system lock is nestable; Cache.commit uses it internally.
    apt_pkg.init()
    with apt_pkg.SystemLock():
        with apt.Cache() as cache:
            current = plan(cache, package, os.uname().release)
            if current != approved:
                raise RuntimeError("The update plan changed. Review the update again; no packages were changed.")
            os.environ["DEBIAN_FRONTEND"] = "noninteractive"
            progress = PackageProgress()
            try:
                if not cache.commit(fetch_progress=DownloadProgress(),
                                    install_progress=progress,
                                    allow_unauthenticated=False):
                    raise RuntimeError("APT did not complete the driver update")
            finally:
                progress.write_stream.close()
                progress.status_stream.close()
            cache.open()
            for change in current["changes"]:
                item = cache[change["package"]] if change["package"] in cache else None
                actual = item.installed.version if item and item.installed else None
                if actual != change["after"]:
                    raise RuntimeError(f"Update incomplete: {change['package']}: expected {change['after']}, found {actual}")
            return current
