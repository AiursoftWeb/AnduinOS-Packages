#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "anduinos-dracut-migration.aosproj"
MIGRATOR = ROOT / "assets/anduinos-dracut-migrate"


class MigrationContractTests(unittest.TestCase):
    def test_bootstrap_package_does_not_create_the_apt_upgrade_deadlock(self) -> None:
        project = PROJECT.read_text()
        self.assertNotIn('<Dependency Include="dracut"', project)
        self.assertNotIn("<Conflicts>", project)
        self.assertIn("anduinos-dracut-migration.timer", project)

    def test_migrator_defers_until_atomic_candidates_and_rejects_removals(self) -> None:
        script = MIGRATOR.read_text()
        self.assertIn("candidate_is_pure_dracut", script)
        self.assertIn("candidate_conflicts_with_legacy_stack", script)
        self.assertIn('"$BOOT_DIR"/vmlinuz-*', script)
        self.assertIn("anduinos-btrfs-snapshots-manager", script)
        self.assertIn("removed_anduinos", script)
        self.assertIn("--simulate", script)
        self.assertIn("lsinitrd", script.lower())

    def test_happy_path_builds_and_validates_each_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            modules = root / "modules" / "6.14.0-test"
            boot = root / "boot"
            state = root / "state"
            bin_dir.mkdir()
            modules.mkdir(parents=True)
            boot.mkdir()
            (boot / "vmlinuz-6.14.0-test").write_text("kernel")

            def executable(name: str, body: str) -> Path:
                path = bin_dir / name
                path.write_text("#!/bin/sh\nset -eu\n" + body)
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
                return path

            migrated = root / "migrated"
            dpkg_query = executable(
                "dpkg-query",
                'for package do :; done\n'
                f'if [ "$package" = initramfs-tools ] && [ ! -e "{migrated}" ]; then printf "ii "; exit 0; fi\n'
                'if [ "$package" = anduinos-btrfs-snapshots-manager ]; then printf "ii "; exit 0; fi\n'
                'exit 1\n',
            )
            apt_cache = executable(
                "apt-cache",
                'case "$1" in\n'
                '  policy) printf "  Candidate: 2.0.2-test\\n" ;;\n'
                '  show) printf "Package: test\\nDepends: dracut, dracut-core\\nConflicts: casper, initramfs-tools, initramfs-tools-core, initramfs-tools-bin, busybox-initramfs, finalrd\\n" ;;\n'
                'esac\n',
            )
            apt_get = executable(
                "apt-get",
                f'case " $* " in\n'
                '  *" --simulate "*) printf "Remv initramfs-tools [old]\\nInst dracut\\n" ;;\n'
                f'  *" install "*) : > "{migrated}" ;;\n'
                'esac\n',
            )
            dracut = executable(
                "dracut",
                'for value in "$@"; do case "$value" in */initrd.img-*) : > "$value" ;; esac; done\n',
            )
            lsinitrd = executable(
                "lsinitrd", '[ -s "$1" ] || [ -e "$1" ]\n',
            )

            env = os.environ.copy()
            env.update(
                {
                    "ANDUINOS_MIGRATION_APT_GET": str(apt_get),
                    "ANDUINOS_MIGRATION_APT_CACHE": str(apt_cache),
                    "ANDUINOS_MIGRATION_DPKG_QUERY": str(dpkg_query),
                    "ANDUINOS_MIGRATION_DRACUT": str(dracut),
                    "ANDUINOS_MIGRATION_LSINITRD": str(lsinitrd),
                    "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                    "ANDUINOS_MIGRATION_BOOT_DIR": str(boot),
                    "ANDUINOS_MIGRATION_STATE_DIR": str(state),
                    "ANDUINOS_MIGRATION_LOCK_FILE": str(root / "lock"),
                }
            )
            result = subprocess.run(
                ["sh", str(MIGRATOR)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            diagnostics = result.stderr + result.stdout
            self.assertTrue(
                (boot / "initrd.img-6.14.0-test").is_file(), diagnostics
            )
            self.assertTrue((state / "complete").is_file(), diagnostics)


if __name__ == "__main__":
    unittest.main()
