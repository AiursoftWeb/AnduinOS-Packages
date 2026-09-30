#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PREINST = ROOT / "scripts/preinst.sh"
POSTINST = ROOT / "scripts/postinst.sh"
VERIFY = ROOT / "assets/anduinos-dracut-verify"
PROOF_MODULE = ROOT / "dracut/99anduinos-migration-proof/module-setup.sh"
PROOF_HOOK = ROOT / "dracut/99anduinos-migration-proof/anduinos-migration-proof.sh"


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class MigrationGuardTests(unittest.TestCase):

    def test_scripts_are_valid_posix_shell(self) -> None:
        for script in (
            PREINST,
            POSTINST,
            VERIFY,
            PROOF_MODULE,
            PROOF_HOOK,
        ):
            with self.subTest(script=script.name):
                subprocess.run(["/bin/sh", "-n", script], check=True)

    def test_live_upgrade_skips_disk_boot_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            marker = root / "live-environment"
            marker.write_text("ANDUINOS_LIVE=1\n", encoding="utf-8")
            env["ANDUINOS_LIVE_MARKER"] = str(marker)
            env["ANDUINOS_MIGRATION_GRUB_MKCONFIG"] = "/bin/false"
            subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.3-2", "2.0.3-3"],
                env=env,
                check=True,
            )
            self.assertFalse((paths["boot"] / "anduinos-dracut-migration").exists())
            self.assertFalse((paths["state"] / "fallback-ready").exists())

            # An unrelated marker must not suppress an installed-system guard.
            marker.write_text("ANDUINOS_LIVE=0\n", encoding="utf-8")
            env["ANDUINOS_MIGRATION_FAIL_AT"] = "before_fallback_kernel"
            result = subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.3-2", "2.0.3-3"],
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 75)

    def migration_environment(self, root: Path) -> tuple[dict[str, str], dict[str, Path]]:
        boot = root / "boot"
        state = root / "state"
        etc = root / "etc"
        bin_dir = root / "bin"
        boot.mkdir()
        state.mkdir()
        bin_dir.mkdir()
        (root / "run/systemd/system").mkdir(parents=True)
        systemctl = executable(
            bin_dir / "systemctl",
            'printf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_LOG"\n',
        )
        (boot / "grub").mkdir()
        (boot / "vmlinuz-7.0.0-test").write_text("legacy-kernel", encoding="utf-8")
        (boot / "initrd.img-7.0.0-test").write_text("legacy-initrd", encoding="utf-8")
        cmdline = root / "cmdline"
        cmdline.write_text(
            "BOOT_IMAGE=/boot/vmlinuz root=UUID=test ro quiet "
            "systemd.unit=system-update.target\n",
            encoding="utf-8",
        )
        boot_id = root / "boot-id"
        boot_id.write_text(
            "11111111-2222-3333-4444-555555555555\n", encoding="utf-8"
        )
        uname = executable(bin_dir / "uname", 'printf "%s\\n" 7.0.0-test\n')
        grub_mkconfig = executable(
            bin_dir / "grub-mkconfig",
            'output=\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = -o ]; then output=$2; shift 2; else shift; fi\n'
            'done\n'
            '[ -n "$output" ]\n'
            'if [ -e "$TEST_EARLY_GENERATOR" ]; then\n'
            '  printf "%s\\n" "menuentry AnduinOS pre-Dracut migration fallback {" '
            '"  linux /anduinos-dracut-migration/fallback-vmlinuz" '
            '"  initrd /anduinos-dracut-migration/fallback-initrd.img" "}" '
            '"menuentry AnduinOS normal boot {" '
            '"  linux /vmlinuz-7.0.0-test" '
            '"  initrd /initrd.img-7.0.0-test" "}" > "$output"\n'
            'else\n'
            '  printf "%s\\n" "menuentry AnduinOS normal boot {" '
            '"  linux /vmlinuz-7.0.0-test" '
            '"  initrd /initrd.img-7.0.0-test" "}" '
            '"menuentry AnduinOS pre-Dracut migration fallback {" '
            '"  linux /anduinos-dracut-migration/fallback-vmlinuz" '
            '"  initrd /anduinos-dracut-migration/fallback-initrd.img" "}" > "$output"\n'
            'fi\n'
        )
        (root / "var/lib/dpkg").mkdir(parents=True)
        env = {
            **os.environ,
            "DPKG_ROOT": str(root),
            "ANDUINOS_LIVE_MARKER": str(root / "run/anduinos-live/environment"),
            "ANDUINOS_MIGRATION_SYSTEMCTL": str(systemctl),
            "TEST_SYSTEMCTL_LOG": str(root / "systemctl-calls"),
            "ANDUINOS_MIGRATION_BOOT_DIR": str(boot),
            "ANDUINOS_MIGRATION_STATE_DIR": str(state),
            "ANDUINOS_MIGRATION_FALLBACK_DIR": str(boot / "anduinos-dracut-migration"),
            "ANDUINOS_MIGRATION_GRUB_GENERATOR": str(etc / "grub.d/06_fallback"),
            "ANDUINOS_MIGRATION_GRUB_GENERATOR_LATE": str(etc / "grub.d/41_fallback"),
            "ANDUINOS_MIGRATION_GRUB_DEFAULT_DROPIN": str(etc / "default/grub.d/99-migration.cfg"),
            "ANDUINOS_MIGRATION_GRUB_CFG": str(boot / "grub/grub.cfg"),
            "ANDUINOS_MIGRATION_PROC_CMDLINE": str(cmdline),
            "ANDUINOS_MIGRATION_GRUB_MKCONFIG": str(grub_mkconfig),
            "ANDUINOS_MIGRATION_UNAME": str(uname),
            "ANDUINOS_MIGRATION_INITRD_INSPECTOR": "/bin/true",
            "ANDUINOS_MIGRATION_BOOT_ID_FILE": str(boot_id),
            "TEST_GRUB_CFG": str(boot / "grub/grub.cfg"),
            "TEST_EARLY_GENERATOR": str(etc / "grub.d/06_fallback"),
        }
        paths = {"boot": boot, "state": state, "etc": etc, "bin": bin_dir}
        return env, paths

    def test_first_install_restores_ubuntu_commands_and_is_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            sbin = root / "usr/sbin"
            sbin.mkdir(parents=True)
            originals = {}
            for command, suffix, wrapper in (
                ("update-initramfs", "anduinos-dracut", "anduinos-update-initramfs"),
                ("update-grub", "anduinos-grub", "anduinos-update-grub"),
            ):
                original = executable(sbin / command, "exit 0\n")
                originals[command] = original.read_bytes()
                subprocess.run([
                    "dpkg-divert", "--root", str(root), "--package", "anduinos-core-system",
                    "--add", "--rename", "--divert", f"/usr/sbin/{command}.{suffix}",
                    f"/usr/sbin/{command}",
                ], check=True, capture_output=True)
                original.symlink_to(f"/usr/libexec/{wrapper}")
            # A missing upstream file must leave the original symlink intact.
            diverted = sbin / "update-initramfs.anduinos-dracut"
            saved = diverted.with_suffix(".saved")
            diverted.rename(saved)
            failed = subprocess.run(["/bin/sh", PREINST, "install"], env=env,
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertTrue((sbin / "update-initramfs").is_symlink())
            saved.rename(diverted)
            # Refuse an administrator's replacement, then resume partial cleanup.
            grub = sbin / "update-grub"
            grub.unlink()
            grub.symlink_to("/administrator/update-grub")
            failed = subprocess.run(["/bin/sh", PREINST, "install"], env=env,
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(os.readlink(grub), "/administrator/update-grub")
            grub.unlink()
            grub.symlink_to("/usr/libexec/anduinos-update-grub")
            for attempt in range(2):
                subprocess.run(["/bin/sh", PREINST, "install"], env=env, check=True)
                for command, content in originals.items():
                    self.assertFalse((sbin / command).is_symlink())
                    self.assertEqual((sbin / command).read_bytes(), content)
                    owner = subprocess.run([
                        "dpkg-divert", "--root", str(root), "--listpackage", f"/usr/sbin/{command}"
                    ], check=True, capture_output=True, text=True).stdout
                    self.assertEqual(owner, "")
            self.assertFalse((paths["state"] / "complete").exists())

    def test_first_tools_install_protects_existing_boot_but_skips_fresh_target(self) -> None:
        for existing_boot in (False, True):
            with self.subTest(existing_boot=existing_boot), tempfile.TemporaryDirectory() as directory:
                env, paths = self.migration_environment(Path(directory))
                if existing_boot:
                    (paths["boot"] / "grub/grub.cfg").write_text("legacy GRUB configuration\n")
                subprocess.run(["/bin/sh", PREINST, "install"], env=env, check=True)
                fallback = paths["boot"] / "anduinos-dracut-migration/fallback-initrd.img"
                self.assertEqual(fallback.exists(), existing_boot)
                if existing_boot:
                    self.assertEqual(fallback.read_text(), "legacy-initrd")
                    self.assertIn(
                        "fallback-initrd.img", (paths["boot"] / "grub/grub.cfg").read_text()
                    )

    def test_preinst_makes_fallback_default_before_returning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env, paths = self.migration_environment(Path(directory))
            subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                env=env,
                check=True,
            )
            fallback = paths["boot"] / "anduinos-dracut-migration"
            self.assertEqual(
                (fallback / "fallback-vmlinuz").read_text(), "legacy-kernel"
            )
            self.assertEqual(
                (fallback / "fallback-initrd.img").read_text(), "legacy-initrd"
            )
            fallback_cmdline = (fallback / "cmdline").read_text()
            self.assertIn("root=UUID=test", fallback_cmdline)
            self.assertNotIn("BOOT_IMAGE", fallback_cmdline)
            self.assertNotIn("system-update.target", fallback_cmdline)
            self.assertTrue((paths["state"] / "fallback-ready").is_file())
            self.assertIn(
                "GRUB_DEFAULT=0",
                (paths["etc"] / "default/grub.d/99-migration.cfg").read_text(),
            )
            generator = paths["etc"] / "grub.d/06_fallback"
            self.assertTrue(generator.is_file())
            self.assertTrue(generator.stat().st_mode & stat.S_IXUSR)

            grub_lib = paths["bin"] / "grub-lib"
            grub_lib.mkdir()
            (grub_lib / "grub-mkconfig_lib").write_text(
                "make_system_path_relative_to_its_root() { printf '%s\\n' \"$1\"; }\n"
                "prepare_grub_to_access_device() { printf '%s\\n' 'search --set=root test'; }\n",
                encoding="utf-8",
            )
            grub_probe = executable(
                paths["bin"] / "grub-probe", 'printf "%s\\n" /dev/test\n'
            )
            generated = subprocess.run(
                ["/bin/sh", generator],
                env={
                    **env,
                    "pkgdatadir": str(grub_lib),
                    "ANDUINOS_MIGRATION_GRUB_PROBE": str(grub_probe),
                },
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            self.assertIn("menuentry 'AnduinOS pre-Dracut migration fallback'", generated)
            self.assertIn(str(fallback / "fallback-vmlinuz"), generated)
            self.assertIn("root=UUID=test ro quiet", generated)

    def test_failed_grub_generation_never_truncates_the_active_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            grub_cfg = paths["boot"] / "grub/grub.cfg"
            grub_cfg.write_text("known-good-grub\n", encoding="utf-8")
            broken = executable(
                paths["bin"] / "broken-grub-mkconfig",
                'output=\n'
                'while [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = -o ]; then output=$2; shift 2; else shift; fi\n'
                'done\n'
                'printf "%s\\n" partial > "$output"\n'
                'exit 1\n',
            )
            failed_env = {
                **env,
                "ANDUINOS_MIGRATION_GRUB_MKCONFIG": str(broken),
            }
            result = subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                env=failed_env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(grub_cfg.read_text(), "known-good-grub\n")


    def test_insufficient_boot_space_aborts_before_package_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            fake_df = executable(
                paths["bin"] / "df",
                'printf "%s\\n" "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
                'printf "%s\\n" "/dev/test 100 99 1 99% /boot"\n',
            )
            result = subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                env={**env, "ANDUINOS_MIGRATION_DF": str(fake_df)},
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((paths["state"] / "fallback-ready").exists())
            self.assertEqual(
                (paths["boot"] / "initrd.img-7.0.0-test").read_text(),
                "legacy-initrd",
            )

    def test_interrupted_preinst_keeps_originals_and_is_retryable(self) -> None:
        checkpoints = (
            "before_fallback_kernel",
            "after_fallback_kernel",
            "after_fallback_initrd",
            "before_space_check",
            "after_space_check",
            "before_manifest",
            "after_manifest",
            "before_update_grub",
            "after_update_grub",
            "after_fallback_ready",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as directory:
                env, paths = self.migration_environment(Path(directory))
                failing = {**env, "ANDUINOS_MIGRATION_FAIL_AT": checkpoint}
                result = subprocess.run(
                    ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                    env=failing,
                    check=False,
                )
                self.assertEqual(result.returncode, 75)
                self.assertEqual(
                    (paths["boot"] / "vmlinuz-7.0.0-test").read_text(),
                    "legacy-kernel",
                )
                self.assertEqual(
                    (paths["boot"] / "initrd.img-7.0.0-test").read_text(),
                    "legacy-initrd",
                )

                subprocess.run(
                    ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                    env=env,
                    check=True,
                )
                self.assertTrue((paths["state"] / "fallback-ready").is_file())

    def test_retry_never_overwrites_the_sealed_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env, paths = self.migration_environment(Path(directory))
            subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                env=env,
                check=True,
            )
            active_initrd = paths["boot"] / "initrd.img-7.0.0-test"
            active_initrd.write_text("later-dracut-image", encoding="utf-8")

            subprocess.run(
                ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                env=env,
                check=True,
            )
            sealed = (
                paths["boot"]
                / "anduinos-dracut-migration/fallback-initrd.img"
            )
            self.assertEqual(sealed.read_text(), "legacy-initrd")

    def test_verifier_stages_before_replacing_the_old_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            modules = root / "modules/7.0.0-test"
            modules.mkdir(parents=True)
            # Removed kernels can leave modules.dep behind. Rebuilding must
            # select bootable kernels, not every residual modules directory.
            for version in ("7.0.0-27-generic", "7.0.0-28-generic", "7.0.0-29-generic"):
                stale = root / "modules" / version
                stale.mkdir()
                (stale / "modules.dep").write_text("kernel/missing.ko.zst:\n")
            calls = root / "dracut-calls"
            dracut = executable(
                paths["bin"] / "dracut",
                f'printf "%s\\n" "$*" >> "{calls}"\n'
                'printf "%s\\n" new-dracut-image > "$2"\n',
            )
            lsinitrd = executable(
                paths["bin"] / "lsinitrd",
                'printf "%s\\n" base rootfs-block anduinos-btrfs-snapshots-manager\n',
            )
            dpkg_query = executable(
                paths["bin"] / "dpkg-query", 'printf "%s" "ii "\n',
            )
            verify_env = {
                **env,
                "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                "ANDUINOS_MIGRATION_DRACUT": str(dracut),
                "ANDUINOS_MIGRATION_LSINITRD": str(lsinitrd),
                "ANDUINOS_MIGRATION_DPKG_QUERY": str(dpkg_query),
                "ANDUINOS_MIGRATION_ROOT_FSTYPE": "btrfs",
            }
            subprocess.run(["/bin/sh", VERIFY, "--rebuild"], env=verify_env, check=True)
            self.assertEqual(calls.read_text().splitlines(), [
                f'--force {paths["boot"]}/.initrd.img-7.0.0-test.anduinos-new 7.0.0-test',
            ])
            self.assertEqual(
                (paths["boot"] / "initrd.img-7.0.0-test").read_text(),
                "new-dracut-image\n",
            )
            subprocess.run(
                ["/bin/sh", VERIFY, "--update-grub"],
                env=verify_env,
                check=True,
            )
            subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"],
                env=verify_env,
                check=True,
            )
            subprocess.run(
                ["/bin/sh", VERIFY, "--verify-running"],
                env=verify_env,
                check=True,
            )

            grub_cfg = paths["boot"] / "grub/grub.cfg"
            grub_cfg.write_text(
                "menuentry fallback {\n"
                "  linux /anduinos-dracut-migration/fallback-vmlinuz\n"
                "  initrd /anduinos-dracut-migration/fallback-initrd.img\n"
                "}\n"
                "menuentry normal {\n"
                "  linux /vmlinuz-7.0.0-test\n"
                "  initrd /initrd.img-7.0.0-test\n"
                "}\n",
                encoding="utf-8",
            )
            default_result = subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"],
                env=verify_env,
                check=False,
            )
            self.assertNotEqual(default_result.returncode, 0)

            grub_cfg.write_text(
                "menuentry wrong-prefix {\n"
                "  linux /vmlinuz-7.0.0-test-extra\n"
                "  initrd /initrd.img-7.0.0-test-extra\n"
                "}\n",
                encoding="utf-8",
            )
            prefix_result = subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"],
                env=verify_env,
                check=False,
            )
            self.assertNotEqual(prefix_result.returncode, 0)

            (paths["boot"] / "initrd.img-7.0.0-test").write_text("known-good")
            failed = {
                **verify_env,
                "ANDUINOS_MIGRATION_DRACUT": "/bin/false",
            }
            result = subprocess.run(
                ["/bin/sh", VERIFY, "--rebuild"], env=failed, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                (paths["boot"] / "initrd.img-7.0.0-test").read_text(),
                "known-good",
            )

    def test_verifier_requires_btrfs_module_while_package_is_configuring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            (root / "modules/7.0.0-test").mkdir(parents=True)
            lsinitrd = executable(
                paths["bin"] / "lsinitrd",
                'printf "%s\\n" base anduinos-migration-proof\n',
            )
            dpkg_query = executable(
                paths["bin"] / "dpkg-query", 'printf "%s" "iU "\n',
            )
            verify_env = {
                **env,
                "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                "ANDUINOS_MIGRATION_LSINITRD": str(lsinitrd),
                "ANDUINOS_MIGRATION_DPKG_QUERY": str(dpkg_query),
                "ANDUINOS_MIGRATION_ROOT_FSTYPE": "btrfs",
            }
            result = subprocess.run(
                ["/bin/sh", VERIFY, "--verify"],
                env=verify_env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_postinst_forces_verified_normal_default_until_confirmed_boot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            (paths["state"] / "fallback-ready").touch()
            early = paths["etc"] / "grub.d/06_fallback"
            early.parent.mkdir(parents=True, exist_ok=True)
            early.write_text("fallback generator", encoding="utf-8")
            dropin = paths["etc"] / "default/grub.d/99-migration.cfg"
            dropin.parent.mkdir(parents=True, exist_ok=True)
            dropin.write_text("GRUB_DEFAULT=0\n", encoding="utf-8")
            calls = root / "verify-calls"
            verifier = executable(
                paths["bin"] / "verify",
                'printf "%s\\n" "$1" >> "$VERIFY_CALLS"\n',
            )
            post_env = {
                **env,
                "ANDUINOS_MIGRATION_VERIFY": str(verifier),
                "VERIFY_CALLS": str(calls),
            }
            subprocess.run(["/bin/sh", POSTINST, "configure", "2.0.2-1"], env=post_env, check=True)
            self.assertEqual(
                calls.read_text().splitlines(),
                ["--rebuild", "--verify", "--verify-default"],
            )
            self.assertFalse(early.exists())
            self.assertTrue((paths["etc"] / "grub.d/41_fallback").is_file())
            self.assertEqual(dropin.read_text(), "GRUB_DEFAULT=0\n")
            self.assertTrue((paths["state"] / "images-verified").is_file())
            self.assertTrue((paths["state"] / "complete").is_file())
            self.assertEqual(
                (root / "systemctl-calls").read_text().splitlines(),
                ["disable --now anduinos-dracut-migration.timer"],
            )

    def test_interrupted_postinst_always_leaves_a_boot_path_and_retries(self) -> None:
        checkpoints = (
            "before_rebuild",
            "after_rebuild",
            "after_images_verified",
            "before_final_update_grub",
            "after_final_update_grub",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env, paths = self.migration_environment(root)
                subprocess.run(
                    ["/bin/sh", PREINST, "upgrade", "2.0.2-1", "2.0.2-3"],
                    env=env,
                    check=True,
                )
                calls = root / "verify-calls"
                verifier = executable(
                    paths["bin"] / "verify",
                    'printf "%s\\n" "$1" >> "$VERIFY_CALLS"\n',
                )
                post_env = {
                    **env,
                    "ANDUINOS_MIGRATION_VERIFY": str(verifier),
                    "VERIFY_CALLS": str(calls),
                    "ANDUINOS_MIGRATION_FAIL_AT": checkpoint,
                }
                result = subprocess.run(
                    ["/bin/sh", POSTINST, "configure", "2.0.2-1"],
                    env=post_env,
                    check=False,
                )
                self.assertEqual(result.returncode, 75)
                config = (paths["boot"] / "grub/grub.cfg").read_text()
                self.assertIn("vmlinuz-7.0.0-test", config)
                self.assertIn("initrd.img-7.0.0-test", config)
                self.assertTrue(
                    (
                        paths["boot"]
                        / "anduinos-dracut-migration/fallback-vmlinuz"
                    ).is_file()
                )
                self.assertTrue(
                    (
                        paths["boot"]
                        / "anduinos-dracut-migration/fallback-initrd.img"
                    ).is_file()
                )

                retry_env = {**post_env, "ANDUINOS_MIGRATION_FAIL_AT": ""}
                subprocess.run(
                    ["/bin/sh", POSTINST, "configure", "2.0.2-1"],
                    env=retry_env,
                    check=True,
                )
                self.assertTrue((paths["state"] / "complete").is_file())

    def test_verification_rejects_empty_images_until_kernel_update_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            for version in ("7.0.0-test", "7.0.0-platform"):
                (root / "modules" / version).mkdir(parents=True)
            (paths["boot"] / "vmlinuz-7.0.0-platform").write_text("new kernel")
            (paths["boot"] / "grub/grub.cfg").write_text(
                "menuentry AnduinOS {\n"
                " linux /boot/vmlinuz-7.0.0-platform\n"
                " initrd /boot/initrd.img-7.0.0-platform\n}\n"
            )
            lsinitrd = executable(
                paths["bin"] / "lsinitrd",
                'printf "%s\\n" base anduinos-migration-proof\n',
            )
            guard_env = {
                **env,
                "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                "ANDUINOS_MIGRATION_LSINITRD": str(lsinitrd),
                "ANDUINOS_MIGRATION_ROOT_FSTYPE": "ext4",
            }
            # Every selected kernel needs a complete image, including when
            # verification runs from another package's maintainer script.
            guard_env["DPKG_MAINTSCRIPT_PACKAGE"] = "nvidia-kernel-common"
            current_image = paths["boot"] / "initrd.img-7.0.0-test"
            current_image.write_text("")
            result = subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"], env=guard_env,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or empty initrd", result.stderr)
            current_image.write_text("generated image")
            result = subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"], env=guard_env,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or empty initrd", result.stderr)
            # Simulate completion of the pending kernel trigger.
            image = paths["boot"] / "initrd.img-7.0.0-platform"
            image.write_text("completed initrd")
            subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"], env=guard_env, check=True,
            )
            image.write_text("")
            result = subprocess.run(
                ["/bin/sh", VERIFY, "--verify-default"], env=guard_env,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_verification_without_migration_proof_still_rejects_live_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, paths = self.migration_environment(root)
            (root / "modules/7.0.0-test").mkdir(parents=True)
            (paths["boot"] / "grub/grub.cfg").write_text(
                "menuentry AnduinOS {\n"
                " linux /boot/vmlinuz-7.0.0-test\n"
                " initrd /boot/initrd.img-7.0.0-test\n}\n"
            )
            lsinitrd = executable(paths["bin"] / "lsinitrd", 'printf "%s\\n" base\n')
            verify_env = {
                **env,
                "ANDUINOS_MIGRATION_MODULES_DIR": str(root / "modules"),
                "ANDUINOS_MIGRATION_LSINITRD": str(lsinitrd),
                "ANDUINOS_MIGRATION_ROOT_FSTYPE": "ext4",
            }
            command = ["/bin/sh", VERIFY, "--verify-default"]
            subprocess.run(command, env=verify_env, check=True)
            for forbidden in (
                "dmsquash-live", "dmsquash-live-autooverlay",
                "livenet", "anduinos-live-layers",
            ):
                with self.subTest(module=forbidden):
                    executable(lsinitrd, f'printf "%s\\n" base {forbidden}\n')
                    result = subprocess.run(
                        command, env=verify_env, capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"contains forbidden Live module {forbidden}", result.stderr)


if __name__ == "__main__":
    unittest.main()
