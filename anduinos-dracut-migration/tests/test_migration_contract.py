#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATOR = ROOT / "assets/anduinos-dracut-migrate"
CONFIRM = ROOT / "assets/anduinos-dracut-confirm-boot"

class MigrationContractTests(unittest.TestCase):

    def test_confirmation_requires_a_later_boot_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            bin_dir = root / "bin"
            state.mkdir()
            bin_dir.mkdir()
            (state / "complete").touch()
            completed = "11111111-2222-3333-4444-555555555555"
            (state / "completed-boot-id").write_text(completed + "\n")
            boot_id = root / "boot-id"
            boot_id.write_text(completed + "\n")
            boot_proof = root / "boot-proof"
            boot_proof.write_text("generator=dracut\nkernel=6.14.0-test\n")
            grub_dropin = root / "etc/default/grub.d/99-migration.cfg"
            grub_dropin.parent.mkdir(parents=True)
            grub_dropin.write_text("GRUB_DEFAULT=0\n")
            calls = root / "verify-calls"
            verifier = bin_dir / "verify"
            verifier.write_text(
                "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$1\" >> \"$VERIFY_CALLS\"\n"
            )
            verifier.chmod(verifier.stat().st_mode | stat.S_IXUSR)
            uname = bin_dir / "uname"
            uname.write_text("#!/bin/sh\nprintf '%s\\n' 6.14.0-test\n")
            uname.chmod(uname.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "ANDUINOS_MIGRATION_STATE_DIR": str(state),
                "ANDUINOS_MIGRATION_VERIFY": str(verifier),
                "ANDUINOS_MIGRATION_BOOT_ID_FILE": str(boot_id),
                "ANDUINOS_MIGRATION_BOOT_PROOF": str(boot_proof),
                "ANDUINOS_MIGRATION_UNAME": str(uname),
                "ANDUINOS_MIGRATION_GRUB_DEFAULT_DROPIN": str(grub_dropin),
                "VERIFY_CALLS": str(calls),
            }

            subprocess.run(["/bin/sh", CONFIRM], env=env, check=True)
            self.assertFalse(calls.exists())
            self.assertFalse((state / "boot-confirmed").exists())

            boot_id.write_text("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n")
            subprocess.run(["/bin/sh", CONFIRM], env=env, check=True)
            self.assertEqual(
                calls.read_text().splitlines(),
                ["--verify-running", "--update-grub"],
            )
            self.assertFalse(grub_dropin.exists())
            self.assertTrue((state / "boot-confirmed").is_file())

    def test_confirmation_rejects_a_later_fallback_boot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            (state / "complete").touch()
            (state / "completed-boot-id").write_text("old-boot\n")
            boot_id = root / "boot-id"
            boot_id.write_text("new-boot\n")
            env = {
                **os.environ,
                "ANDUINOS_MIGRATION_STATE_DIR": str(state),
                "ANDUINOS_MIGRATION_BOOT_ID_FILE": str(boot_id),
                "ANDUINOS_MIGRATION_BOOT_PROOF": str(root / "missing-proof"),
                "ANDUINOS_MIGRATION_VERIFY": "/bin/true",
            }
            result = subprocess.run(["/bin/sh", CONFIRM], env=env, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((state / "boot-confirmed").exists())

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
            unexpected_update = root / "unexpected-update"
            timer_disabled = root / "timer-disabled"
            systemd_runtime = root / "run/systemd/system"
            systemd_runtime.mkdir(parents=True)
            boot_id = root / "boot-id"
            boot_id.write_text("11111111-2222-3333-4444-555555555555\n")
            dpkg_query = executable(
                "dpkg-query",
                'for package do :; done\n'
                f'if [ "$package" = initramfs-tools ] && [ ! -e "{migrated}" ]; then printf "ii "; exit 0; fi\n'
                'if [ "$package" = anduinos-btrfs-snapshots-manager ]; then printf "ii "; exit 0; fi\n'
                'if [ "$package" = plymouth-anduinos ]; then printf "ii "; exit 0; fi\n'
                'exit 1\n',
            )
            apt_cache = executable(
                "apt-cache",
                'case "$1" in\n'
                '  policy) printf "  Candidate: 2.0.2-test\\n" ;;\n'
                '  show) printf "Package: test\\nDepends: anduinos-core-system, dracut, dracut-core\\nConflicts: casper, initramfs-tools, initramfs-tools-core, initramfs-tools-bin, busybox-initramfs, finalrd\\n" ;;\n'
                'esac\n',
            )
            apt_get = executable(
                "apt-get",
                f'case " $* " in\n'
                f'  *" update "*) : > "{unexpected_update}"; exit 1 ;;\n'
                '  *" --simulate "*)\n'
                '    printf "Remv initramfs-tools [old]\\nInst dracut\\n"\n'
                '    [ -z "${TEST_UNSAFE_REMOVAL:-}" ] || '
                'printf "Remv %s [old]\\n" "$TEST_UNSAFE_REMOVAL"\n'
                '    ;;\n'
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
            verify_calls = root / "verify-calls"
            verify = executable(
                "verify",
                f'printf "%s\\n" "$1" >> "{verify_calls}"\n'
                f'[ "$1" != --rebuild ] || : > "{boot / "initrd.img-6.14.0-test"}"\n',
            )
            systemctl = executable(
                "systemctl", f': > "{timer_disabled}"\n',
            )

            env = os.environ.copy()
            env.update(
                {
                    "ANDUINOS_MIGRATION_APT_GET": str(apt_get),
                    "ANDUINOS_MIGRATION_APT_CACHE": str(apt_cache),
                    "ANDUINOS_MIGRATION_DPKG_QUERY": str(dpkg_query),
                    "ANDUINOS_MIGRATION_DRACUT": str(dracut),
                    "ANDUINOS_MIGRATION_LSINITRD": str(lsinitrd),
                    "ANDUINOS_MIGRATION_VERIFY": str(verify),
                    "ANDUINOS_MIGRATION_SYSTEMCTL": str(systemctl),
                    "ANDUINOS_MIGRATION_SYSTEMD_RUNTIME_DIR": str(systemd_runtime),
                    "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                    "ANDUINOS_MIGRATION_BOOT_DIR": str(boot),
                    "ANDUINOS_MIGRATION_STATE_DIR": str(state),
                    "ANDUINOS_MIGRATION_LOCK_FILE": str(root / "lock"),
                    "ANDUINOS_MIGRATION_BOOT_ID_FILE": str(boot_id),
                }
            )
            unsafe = subprocess.run(
                ["sh", str(MIGRATOR)],
                env={**env, "TEST_UNSAFE_REMOVAL": "plymouth-anduinos"},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(unsafe.returncode, 0, unsafe.stderr + unsafe.stdout)
            self.assertIn("unexpected package removals", unsafe.stdout)
            self.assertFalse(migrated.exists())

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
            self.assertEqual(
                (state / "completed-boot-id").read_text().strip(),
                "11111111-2222-3333-4444-555555555555",
            )
            self.assertFalse(unexpected_update.exists(), diagnostics)
            self.assertTrue(timer_disabled.is_file(), diagnostics)
            self.assertEqual(
                verify_calls.read_text().splitlines(),
                ["--rebuild", "--verify", "--verify-default"],
            )

    def test_missing_legacy_stack_does_not_bypass_image_validation(self) -> None:
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

            dpkg_query = executable(
                "dpkg-query",
                'for package do :; done\n'
                'case "$*" in\n'
                '  *Status-Abbrev*)\n'
                '    case "$package" in\n'
                '      initramfs-tools) exit 1 ;;\n'
                '      *) printf "ii ";;\n'
                '    esac ;;\n'
                '  *Version*) printf "2.0.2-test" ;;\n'
                'esac\n',
            )
            apt_cache = executable(
                "apt-cache",
                'case "$1" in\n'
                '  policy) printf "  Candidate: 2.0.2-test\\n" ;;\n'
                '  show) printf "Package: test\\nDepends: anduinos-core-system, dracut, dracut-core\\nConflicts: casper, initramfs-tools, initramfs-tools-core, initramfs-tools-bin, busybox-initramfs, finalrd\\n" ;;\n'
                'esac\n',
            )

            env = os.environ.copy()
            boot_id = root / "boot-id"
            boot_id.write_text("11111111-2222-3333-4444-555555555555\n")
            env.update(
                {
                    "ANDUINOS_MIGRATION_APT_CACHE": str(apt_cache),
                    "ANDUINOS_MIGRATION_DPKG_QUERY": str(dpkg_query),
                    "ANDUINOS_MIGRATION_DRACUT": "/bin/false",
                    "ANDUINOS_MIGRATION_LSINITRD": "/bin/true",
                    "ANDUINOS_MIGRATION_VERIFY": "/bin/false",
                    "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                    "ANDUINOS_MIGRATION_BOOT_DIR": str(boot),
                    "ANDUINOS_MIGRATION_STATE_DIR": str(state),
                    "ANDUINOS_MIGRATION_LOCK_FILE": str(root / "lock"),
                    "ANDUINOS_MIGRATION_BOOT_ID_FILE": str(boot_id),
                }
            )
            result = subprocess.run(
                ["sh", str(MIGRATOR)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertFalse((state / "complete").exists())

if __name__ == "__main__":
    unittest.main()
