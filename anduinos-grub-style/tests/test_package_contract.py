#!/usr/bin/env python3

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT / "assets/20-anduinos-style.cfg"
POSTINST = PROJECT / "scripts/postinst.sh"
POSTRM = PROJECT / "scripts/postrm.sh"

CONFIG_TEXT = """# Prefer a lower graphics mode while keeping GRUB's trusted default Unicode font.
GRUB_GFXMODE="1440x900,1280x800,1280x720,1024x768,auto"
# Let Linux and Plymouth select their own platform-appropriate video mode.
GRUB_GFXPAYLOAD_LINUX="auto"
"""


def write_fake_command(directory: Path, name: str, body: str) -> Path:
    command = directory / name
    command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    return command


def install_fake_chroot_detectors(
    directory: Path, systemd_result: int = 1, ischroot_result: int = 1
) -> None:
    write_fake_command(directory, "systemd-detect-virt", f"exit {systemd_result}")
    write_fake_command(directory, "ischroot", f"exit {ischroot_result}")


class GrubStylePackageContractTests(unittest.TestCase):
    def test_maintainer_scripts_have_valid_posix_shell_syntax(self) -> None:
        for script in (POSTINST, POSTRM):
            with self.subTest(script=script.name):
                subprocess.run(["/bin/sh", "-n", script], check=True)

    def test_maintainer_script_action_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            log = fake_bin / "calls.log"
            write_fake_command(
                fake_bin,
                "update-grub",
                'printf "%s\\n" update-grub >> "$UPDATE_GRUB_LOG"',
            )
            install_fake_chroot_detectors(fake_bin)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "UPDATE_GRUB_LOG": str(log),
            }

            cases = (
                (POSTINST, "configure", 1, False),
                (POSTINST, "abort-upgrade", 0, False),
                (POSTRM, "remove", 1, True),
                (POSTRM, "purge", 1, True),
                (POSTRM, "upgrade", 0, False),
            )
            for script, action, expected_calls, removes_config in cases:
                with self.subTest(script=script.name, action=action):
                    config = (
                        test_root
                        / "etc/default/grub.d/20-anduinos-style.cfg"
                    )
                    config.parent.mkdir(parents=True, exist_ok=True)
                    config.write_text(CONFIG_TEXT, encoding="utf-8")
                    log.unlink(missing_ok=True)
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                    calls = (
                        log.read_text(encoding="utf-8").splitlines()
                        if log.exists()
                        else []
                    )
                    self.assertEqual(calls, ["update-grub"] * expected_calls)
                    self.assertEqual(config.exists(), not removes_config)

    def test_chroot_defers_update_grub(self) -> None:
        for detector, systemd_result, ischroot_result in (
            ("systemd-detect-virt", 0, 1),
            ("ischroot", 1, 0),
        ):
            with self.subTest(detector=detector), tempfile.TemporaryDirectory() as temp_dir:
                fake_bin = Path(temp_dir) / "bin"
                fake_bin.mkdir()
                log = fake_bin / "calls.log"
                write_fake_command(
                    fake_bin,
                    "update-grub",
                    'printf "%s\\n" update-grub >> "$UPDATE_GRUB_LOG"',
                )
                install_fake_chroot_detectors(
                    fake_bin,
                    systemd_result=systemd_result,
                    ischroot_result=ischroot_result,
                )
                env = {
                    **os.environ,
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "UPDATE_GRUB_LOG": str(log),
                }
                result = subprocess.run(
                    ["/bin/sh", POSTINST, "configure"],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertFalse(log.exists())
                self.assertIn("deferring GRUB configuration refresh", result.stdout)

    def test_update_grub_failure_is_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            write_fake_command(fake_bin, "update-grub", "exit 23")
            install_fake_chroot_detectors(fake_bin)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            for script, action in ((POSTINST, "configure"), (POSTRM, "remove")):
                with self.subTest(script=script.name):
                    result = subprocess.run(["/bin/sh", script, action], env=env)
                    self.assertEqual(result.returncode, 23)


if __name__ == "__main__":
    unittest.main()
