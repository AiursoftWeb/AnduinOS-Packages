#!/usr/bin/env python3
"""Verify that a built installer package contains the VM-test contract."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


LIB = Path("usr/lib/anduinos-installer-beta")
REQUIRED_FILES = (
    LIB / "executor_cli.py",
    LIB / "guided_test_plan_cli.py",
    LIB / "guided_test_evidence_cli.py",
    LIB / "installer_core/destructive_test.py",
    LIB / "installer_core/execution_boundaries.py",
    LIB / "installer_core/guided_evidence.py",
    LIB / "installer_core/guided_test_plan.py",
    Path("usr/bin/anduinos-installer-executor"),
    Path("usr/bin/anduinos-installer-storage-probe"),
    Path("usr/share/polkit-1/actions/com.anduinos.installer-beta.policy"),
    Path("usr/share/anduinos-installer-beta/style.css"),
    Path("usr/share/anduinos-installer-beta/icons/welcome.svg"),
    Path("usr/share/anduinos-installer-beta/icons/keyboard.svg"),
    Path("usr/share/anduinos-installer-beta/icons/updates.svg"),
    Path("usr/share/anduinos-installer-beta/icons/disk.svg"),
    Path("usr/share/anduinos-installer-beta/icons/timeback.svg"),
    Path("usr/share/anduinos-installer-beta/icons/coexistence.svg"),
    Path("usr/share/anduinos-installer-beta/icons/account.svg"),
    Path("usr/share/anduinos-installer-beta/icons/timezone.svg"),
    Path("usr/share/anduinos-installer-beta/icons/review.svg"),
    Path("usr/share/anduinos-installer-beta/icons/advanced.svg"),
    Path("usr/share/anduinos-installer-beta/icons/btrfs.svg"),
    Path("usr/share/anduinos-installer-beta/icons/ext4.svg"),
    Path("usr/share/anduinos-installer-beta/icons/flashing-disk.svg"),
    Path("usr/share/anduinos-installer-beta/icons/how-should-use.svg"),
    Path("usr/share/anduinos-installer-beta/icons/one-single-disk.svg"),
    Path("usr/share/anduinos-installer-beta/icons/select-installation-disk.svg"),
)
FORBIDDEN_PUBLIC_LAUNCHERS = (
    Path("usr/bin/guided-test-plan"),
    Path("usr/bin/guided-test-evidence"),
)
REQUIRED_DEPENDENCIES = {
    "python3",
    "parted",
    "dosfstools",
    "efibootmgr",
    "util-linux",
    "polkitd",
}


def verify_staged_root(root: Path) -> dict[str, object]:
    missing = []
    for relative in REQUIRED_FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            missing.append("/" + relative.as_posix())
    if missing:
        raise RuntimeError("Built package is missing: " + ", ".join(missing))
    forbidden = [
        "/" + relative.as_posix()
        for relative in FORBIDDEN_PUBLIC_LAUNCHERS
        if (root / relative).exists()
    ]
    if forbidden:
        raise RuntimeError(
            "Internal VM tools gained public launchers: "
            + ", ".join(forbidden)
        )
    wrapper = root / "usr/bin/anduinos-installer-executor"
    if not stat.S_IMODE(wrapper.stat().st_mode) & 0o111:
        raise RuntimeError("Public executor wrapper is not executable")
    launcher = wrapper.read_text()
    if 'if [ "$#" -ne 0 ]' not in launcher or 'executor_cli.py "$@"' in launcher:
        raise RuntimeError("Public executor wrapper can forward test arguments")
    storage_probe = root / "usr/bin/anduinos-installer-storage-probe"
    if not stat.S_IMODE(storage_probe.stat().st_mode) & 0o111:
        raise RuntimeError("Storage probe wrapper is not executable")
    probe_launcher = storage_probe.read_text()
    if 'if [ "$#" -ne 1 ]' not in probe_launcher:
        raise RuntimeError("Storage probe wrapper does not enforce one argument")
    caches = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name == "__pycache__"
        or path.suffix in {".pyc", ".pyo"}
    )
    if caches:
        raise RuntimeError("Built package contains Python caches")
    return {
        "required_files": len(REQUIRED_FILES),
        "public_test_launchers": 0,
        "python_caches": 0,
    }


def package_dependencies(package: Path) -> set[str]:
    result = subprocess.run(
        ("dpkg-deb", "--field", str(package), "Depends"),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return parse_dependencies(result.stdout)


def parse_dependencies(value: str) -> set[str]:
    return {
        item.strip().split(None, 1)[0].split(":", 1)[0]
        for item in value.split(",")
        if item.strip()
    }


def verify_private_cli_loads(root: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / LIB)
    for name in ("guided_test_plan_cli.py", "guided_test_evidence_cli.py"):
        result = subprocess.run(
            (sys.executable, str(root / LIB / name), "--help"),
            capture_output=True,
            text=True,
            env=environment,
            check=False,
            timeout=30,
        )
        if result.returncode != 0 or "usage:" not in result.stdout.casefold():
            raise RuntimeError(f"Packaged private CLI cannot load: {name}")


def verify_package(package: Path) -> dict[str, object]:
    if package.is_symlink() or not package.is_file():
        raise RuntimeError(f"Package is not a regular file: {package}")
    package = package.resolve()
    if shutil.which("dpkg-deb") is None:
        raise RuntimeError("dpkg-deb is required to inspect the package")
    dependencies = package_dependencies(package)
    missing_dependencies = sorted(REQUIRED_DEPENDENCIES - dependencies)
    if missing_dependencies:
        raise RuntimeError(
            "Built package is missing dependencies: "
            + ", ".join(missing_dependencies)
        )
    with tempfile.TemporaryDirectory(prefix="anduinos-package-check-") as temp:
        root = Path(temp) / "root"
        subprocess.run(
            ("dpkg-deb", "--extract", str(package), str(root)),
            check=True,
            timeout=120,
        )
        result = verify_staged_root(root)
        verify_private_cli_loads(root)
    result["dependencies"] = sorted(REQUIRED_DEPENDENCIES)
    return result


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args(arguments)
    try:
        print(json.dumps(verify_package(args.package), sort_keys=True))
        return 0
    except Exception as error:
        print(f"Package verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
