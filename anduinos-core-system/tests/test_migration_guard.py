#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "anduinos-core-system.aosproj"
PREINST = ROOT / "scripts/preinst.sh"
POSTINST = ROOT / "scripts/postinst.sh"
VERIFY = ROOT / "assets/anduinos-dracut-verify"
UPDATE_INITRAMFS_GUARD = ROOT / "assets/anduinos-update-initramfs"
UPDATE_GRUB_GUARD = ROOT / "assets/anduinos-update-grub"
PRERM = ROOT / "scripts/prerm.sh"
PROOF_MODULE = ROOT / "dracut/99anduinos-migration-proof/module-setup.sh"
PROOF_HOOK = ROOT / "dracut/99anduinos-migration-proof/anduinos-migration-proof.sh"


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class MigrationGuardTests(unittest.TestCase):
    def test_real_package_wires_both_synchronous_guards(self) -> None:
        project = ET.parse(PROJECT).getroot()
        self.assertEqual(
            project.findtext(".//PackageVersion"),
            "2.0.2-5+$(SuiteShortName)",
        )
        self.assertEqual(
            project.find(".//PreInstallScript").get("Include"),
            "scripts/preinst.sh",
        )
        self.assertEqual(
            project.find(".//PostInstallScript").get("Include"),
            "scripts/postinst.sh",
        )
        helper = project.find(".//IncludeScript")
        self.assertEqual(helper.get("Include"), "assets/anduinos-dracut-verify")
        self.assertEqual(helper.get("Target"), "/usr/libexec/anduinos-dracut-verify")
        included_targets = {
            item.get("Target") for item in project.findall(".//IncludeScript")
        }
        self.assertIn(
            "/usr/lib/dracut/modules.d/99anduinos-migration-proof/module-setup.sh",
            included_targets,
        )
        self.assertIn(
            "/usr/lib/dracut/modules.d/99anduinos-migration-proof/anduinos-migration-proof.sh",
            included_targets,
        )
        self.assertIn("/usr/libexec/anduinos-update-initramfs", included_targets)
        self.assertIn("/usr/libexec/anduinos-update-grub", included_targets)
        self.assertEqual(project.findall(".//DpkgTrigger"), [])
        self.assertEqual(
            project.find(".//PreRemoveScript").get("Include"),
            "scripts/prerm.sh",
        )

    def test_scripts_are_valid_posix_shell(self) -> None:
        for script in (
            PREINST,
            POSTINST,
            PRERM,
            VERIFY,
            UPDATE_INITRAMFS_GUARD,
            UPDATE_GRUB_GUARD,
            PROOF_MODULE,
            PROOF_HOOK,
        ):
            with self.subTest(script=script.name):
                subprocess.run(["/bin/sh", "-n", script], check=True)

    def migration_environment(self, root: Path) -> tuple[dict[str, str], dict[str, Path]]:
        boot = root / "boot"
        state = root / "state"
        etc = root / "etc"
        bin_dir = root / "bin"
        boot.mkdir()
        state.mkdir()
        bin_dir.mkdir()
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
        update_initramfs = executable(
            bin_dir / "update-initramfs",
            'printf "%s\\n" real-dracut-wrapper\n',
        )
        update_initramfs_divert = bin_dir / "update-initramfs.anduinos-dracut"
        update_grub = executable(
            bin_dir / "update-grub",
            'printf "%s\\n" real-update-grub\n',
        )
        update_grub_divert = bin_dir / "update-grub.anduinos-grub"
        dpkg_divert = executable(
            bin_dir / "dpkg-divert",
            'action=\ndivert=\noriginal=\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    --add|--remove) action=$1; shift ;;\n'
            '    --listpackage) action=$1; original=$2; shift 2 ;;\n'
            '    --divert) divert=$2; shift 2 ;;\n'
            '    --package) shift 2 ;;\n'
            '    --rename) shift ;;\n'
            '    *) original=$1; shift ;;\n'
            '  esac\n'
            'done\n'
            'case "$action" in\n'
            '  --add) [ -e "$divert" ] || mv "$original" "$divert" ;;\n'
            '  --remove) [ ! -e "$divert" ] || mv "$divert" "$original" ;;\n'
            '  --listpackage)\n'
            '    for candidate in "$original".anduinos-*; do\n'
            '      [ ! -e "$candidate" ] || { printf "%s\\n" anduinos-core-system; break; }\n'
            '    done\n'
            '    ;;\n'
            'esac\n',
        )
        env = {
            **os.environ,
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
            "ANDUINOS_MIGRATION_UPDATE_INITRAMFS": str(update_initramfs),
            "ANDUINOS_MIGRATION_UPDATE_INITRAMFS_DIVERT": str(
                update_initramfs_divert
            ),
            "ANDUINOS_MIGRATION_UPDATE_INITRAMFS_WRAPPER": str(
                UPDATE_INITRAMFS_GUARD
            ),
            "ANDUINOS_MIGRATION_UPDATE_GRUB": str(update_grub),
            "ANDUINOS_MIGRATION_UPDATE_GRUB_DIVERT": str(update_grub_divert),
            "ANDUINOS_MIGRATION_UPDATE_GRUB_WRAPPER": str(UPDATE_GRUB_GUARD),
            "ANDUINOS_MIGRATION_DPKG_DIVERT": str(dpkg_divert),
            "TEST_GRUB_CFG": str(boot / "grub/grub.cfg"),
            "TEST_EARLY_GENERATOR": str(etc / "grub.d/06_fallback"),
        }
        paths = {"boot": boot, "state": state, "etc": etc, "bin": bin_dir}
        return env, paths

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

            wrapper_result = subprocess.run(
                ["/bin/sh", UPDATE_GRUB_GUARD],
                env=failed_env,
                check=False,
            )
            self.assertNotEqual(wrapper_result.returncode, 0)
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
            dracut = executable(
                paths["bin"] / "dracut",
                'printf "%s\\n" new-dracut-image > "$2"\n',
            )
            lsinitrd = executable(
                paths["bin"] / "lsinitrd",
                'printf "%s\\n" base rootfs-block anduinos-migration-proof anduinos-btrfs-snapshots-manager\n',
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
                (paths["bin"] / "update-initramfs").read_bytes(),
                UPDATE_INITRAMFS_GUARD.read_bytes(),
            )
            self.assertTrue((paths["bin"] / "update-initramfs").is_symlink())
            self.assertTrue(
                (paths["bin"] / "update-initramfs.anduinos-dracut").is_file()
            )
            self.assertEqual(
                (paths["bin"] / "update-grub").read_bytes(),
                UPDATE_GRUB_GUARD.read_bytes(),
            )
            self.assertTrue((paths["bin"] / "update-grub").is_symlink())
            self.assertTrue(
                (paths["bin"] / "update-grub.anduinos-grub").is_file()
            )

            subprocess.run(["/bin/sh", PRERM, "remove"], env=post_env, check=True)
            subprocess.run(["/bin/sh", PRERM, "remove"], env=post_env, check=True)
            self.assertIn(
                "real-dracut-wrapper",
                (paths["bin"] / "update-initramfs").read_text(),
            )
            self.assertFalse(
                (paths["bin"] / "update-initramfs.anduinos-dracut").exists()
            )
            self.assertIn(
                "real-update-grub",
                (paths["bin"] / "update-grub").read_text(),
            )
            self.assertFalse(
                (paths["bin"] / "update-grub.anduinos-grub").exists()
            )

    def test_fresh_configure_installs_the_dracut_guard_without_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env, paths = self.migration_environment(Path(directory))
            subprocess.run(["/bin/sh", POSTINST, "configure"], env=env, check=True)
            self.assertEqual(
                (paths["bin"] / "update-initramfs").read_bytes(),
                UPDATE_INITRAMFS_GUARD.read_bytes(),
            )
            self.assertEqual(
                (paths["bin"] / "update-grub").read_bytes(),
                UPDATE_GRUB_GUARD.read_bytes(),
            )
            self.assertFalse((paths["state"] / "complete").exists())

    def test_interrupted_postinst_always_leaves_a_boot_path_and_retries(self) -> None:
        checkpoints = (
            "before_update_initramfs_divert",
            "after_update_initramfs_divert",
            "after_update_initramfs_guard",
            "before_update_grub_divert",
            "after_update_grub_divert",
            "after_update_grub_guard",
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

    def test_diverted_update_initramfs_runs_the_final_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            real_calls = root / "real-calls"
            verify_calls = root / "verify-calls"
            real = executable(
                bin_dir / "real-update-initramfs",
                'printf "%s\\n" "$*" >> "$REAL_CALLS"\n',
            )
            verifier = executable(
                bin_dir / "verify",
                'printf "%s\\n" "$1" >> "$VERIFY_CALLS"\n',
            )
            guard_env = {
                **os.environ,
                "ANDUINOS_UPDATE_INITRAMFS_REAL": str(real),
                "ANDUINOS_MIGRATION_VERIFY": str(verifier),
                "REAL_CALLS": str(real_calls),
                "VERIFY_CALLS": str(verify_calls),
            }
            subprocess.run(
                ["/bin/sh", UPDATE_INITRAMFS_GUARD, "-u", "-k", "all"],
                env=guard_env,
                check=True,
            )
            self.assertEqual(real_calls.read_text().splitlines(), ["-u -k all"])
            self.assertEqual(verify_calls.read_text().splitlines(), ["--verify"])

            subprocess.run(
                ["/bin/sh", UPDATE_INITRAMFS_GUARD, "-u"],
                env={**guard_env, "DPKG_MAINTSCRIPT_PACKAGE": "test-package"},
                check=True,
            )
            self.assertEqual(verify_calls.read_text().splitlines(), ["--verify"])

            subprocess.run(
                ["/bin/sh", UPDATE_INITRAMFS_GUARD, "-u"],
                env={**guard_env, "DPKG_MAINTSCRIPT_PACKAGE": ""},
                check=True,
            )
            self.assertEqual(verify_calls.read_text().splitlines(), ["--verify"])

            subprocess.run(
                ["/bin/sh", UPDATE_INITRAMFS_GUARD, "-d", "-k", "old"],
                env=guard_env,
                check=True,
            )
            self.assertEqual(verify_calls.read_text().splitlines(), ["--verify"])


if __name__ == "__main__":
    unittest.main()
