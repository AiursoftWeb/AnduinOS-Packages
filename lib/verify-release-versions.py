#!/usr/bin/env python3
"""Verify that user-visible application versions match the OS release."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OS_RELEASE = ROOT / "base-files/assets/resolute/os-release"

PYTHON_ABOUT_SOURCES = (
    ROOT / "anduinos-appearance/src/anduinos-appearance",
    ROOT / "anduinos-driver-center/src/anduinos_driver_center/app.py",
    ROOT / "anduinos-oobe/assets/anduinos-oobe",
    ROOT / "anduinos-control-panel/src/anduinos_control_panel/app.py",
    ROOT / "anduinos-whisper-gtk/src/anduinos_whisper_gtk/app.py",
)

CARGO_MANIFESTS = (
    ROOT / "anduinos-swapcontrol-gtk/Cargo.toml",
    ROOT / "anduinos-ufwall-gtk/Cargo.toml",
    ROOT / "anduinos-yubikey-manager/Cargo.toml",
    ROOT / "anduinos-btrfs-snapshots-manager/src/Cargo.toml",
)

CARGO_LOCK_PACKAGES = {
    ROOT / "anduinos-swapcontrol-gtk/Cargo.lock": {"swapcontrol-gtk"},
    ROOT / "anduinos-ufwall-gtk/Cargo.lock": {"ufwall-gtk"},
    ROOT / "anduinos-yubikey-manager/Cargo.lock": {"anduinos-yubikey-manager"},
    ROOT / "anduinos-btrfs-snapshots-manager/src/Cargo.lock": {
        "anduinos-btrfs-snapshots-manager",
        "anduinos-btrfs-snapshots-manager-helper",
        "anduinos-btrfs-snapshots-manager-notifier",
        "anduinos-btrfs-snapshots-manager-scheduler",
        "anduinos-recovery-engine",
        "snapshots-manager-common",
    },
}


def release_version() -> str:
    match = re.search(
        r'^VERSION_ID="?([^"\n]+)"?$',
        OS_RELEASE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"VERSION_ID is missing from {OS_RELEASE}")
    return match.group(1)


def verify_python_about_sources(expected: str) -> None:
    pattern = re.compile(r"\.set_version\([\"']([^\"']+)[\"']\)")
    for path in PYTHON_ABOUT_SOURCES:
        versions = pattern.findall(path.read_text(encoding="utf-8"))
        if versions != [expected]:
            raise AssertionError(
                f"{path.relative_to(ROOT)} exposes {versions or 'no version'}; "
                f"expected exactly one {expected!r} About version"
            )


def verify_installer_version(expected: str) -> None:
    path = ROOT / "anduinos-installer-beta/src/__init__.py"
    match = re.search(
        r"^VERSION\s*=\s*['\"]([^'\"]+)['\"]$",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    actual = match.group(1) if match else None
    if actual != expected:
        raise AssertionError(
            f"{path.relative_to(ROOT)} declares {actual!r}; expected {expected!r}"
        )


def verify_cargo_manifests(expected: str) -> None:
    for path in CARGO_MANIFESTS:
        with path.open("rb") as stream:
            manifest = tomllib.load(stream)
        section = manifest.get("package") or manifest.get("workspace", {}).get("package")
        actual = section.get("version") if section else None
        if actual != expected:
            raise AssertionError(
                f"{path.relative_to(ROOT)} declares {actual!r}; expected {expected!r}"
            )


def verify_cargo_locks(expected: str) -> None:
    for path, package_names in CARGO_LOCK_PACKAGES.items():
        with path.open("rb") as stream:
            lockfile = tomllib.load(stream)
        versions = {
            package["name"]: package["version"]
            for package in lockfile.get("package", [])
            if package["name"] in package_names
        }
        missing = package_names - versions.keys()
        mismatched = {
            name: version for name, version in versions.items() if version != expected
        }
        if missing or mismatched:
            raise AssertionError(
                f"{path.relative_to(ROOT)} missing={sorted(missing)} "
                f"mismatched={mismatched}; expected {expected!r}"
            )


def main() -> None:
    expected = release_version()
    verify_python_about_sources(expected)
    verify_installer_version(expected)
    verify_cargo_manifests(expected)
    verify_cargo_locks(expected)
    print(f"Release-version policy passed: all user-visible versions are {expected}")


if __name__ == "__main__":
    main()
