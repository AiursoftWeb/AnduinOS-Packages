import tempfile
import unittest
from pathlib import Path
import subprocess

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.software import (
    MULTIMEDIA_CODECS_PACKAGE,
    InstallMultimediaCodecsStep,
    InstallThirdPartyDriversStep,
    RefreshPackageIndexesStep,
    UpgradeSystemStep,
)
from installer_core.steps import InstallContext, StepSkipped, StepWarning


def context_for(target: Path, *, updates: bool = True) -> InstallContext:
    return InstallContext(
        valid_plan(install_updates=updates),
        lambda _message: None,
        {"target": target, "chroot_environment_ready": True},
    )


def prepare_apt(target: Path) -> None:
    apt_get = target / "usr/bin/apt-get"
    apt_get.parent.mkdir(parents=True, exist_ok=True)
    apt_get.touch()


class CodecInstallRunner(FakeRunner):
    """Report the codec metapackage missing once, then installed."""

    def __init__(self):
        super().__init__()
        self.codec_queries = 0

    def run(self, command, **kwargs):
        command = tuple(command)
        if "dpkg-query" in command and command[-1] == MULTIMEDIA_CODECS_PACKAGE:
            self.commands.append((command, kwargs))
            self.codec_queries += 1
            if self.codec_queries == 1:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return subprocess.CompletedProcess(command, 0, "ii ", "")
        return super().run(command, **kwargs)


class PackageUpdateTests(unittest.TestCase):
    def test_selected_multimedia_formats_install_one_owned_metapackage(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = CodecInstallRunner()
            context = InstallContext(
                valid_plan(install_multimedia_codecs=True),
                lambda _message: None,
                {
                    "target": target,
                    "chroot_environment_ready": True,
                    "network_online": True,
                },
            )
            step = InstallMultimediaCodecsStep(runner)
            step.execute(context)
            step.verify(context)

        self.assertTrue(context.values["multimedia_codecs_installed"])
        install_commands = [
            command
            for command, _kwargs in runner.commands
            if command[-2:] == ("install", MULTIMEDIA_CODECS_PACKAGE)
        ]
        self.assertEqual(len(install_commands), 1)
        self.assertIn("--no-install-recommends", install_commands[0])
        self.assertTrue(context.values["package_indexes_refreshed"])

    def test_unselected_multimedia_formats_do_not_probe_or_run_apt(self):
        runner = FakeRunner()
        context = InstallContext(
            valid_plan(install_multimedia_codecs=False),
            lambda _message: None,
        )
        with self.assertRaisesRegex(StepSkipped, "not selected"):
            InstallMultimediaCodecsStep(runner).execute(context)
        self.assertEqual(runner.commands, [])

    def test_selected_multimedia_formats_warn_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            context = InstallContext(
                valid_plan(install_multimedia_codecs=True),
                lambda _message: None,
                {
                    "target": target,
                    "chroot_environment_ready": True,
                    "network_online": False,
                },
            )
            with self.assertRaisesRegex(StepWarning, "offline"):
                InstallMultimediaCodecsStep(runner).execute(context)

        self.assertEqual(len(runner.commands), 1)
        self.assertIn("dpkg-query", runner.commands[0][0])
        self.assertFalse(
            any("apt-get" in command for command, _kwargs in runner.commands)
        )

    def test_clean_multimedia_download_failure_is_only_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = CodecInstallRunner()
            context = InstallContext(
                valid_plan(install_multimedia_codecs=True),
                lambda _message: None,
                {
                    "target": target,
                    "chroot_environment_ready": True,
                    "network_online": True,
                    "package_indexes_refreshed": True,
                },
            )
            install = (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "--no-install-recommends",
                "-o",
                "Acquire::Retries=1",
                "-o",
                "Acquire::http::Timeout=15",
                "-o",
                "Acquire::https::Timeout=15",
                "install",
                MULTIMEDIA_CODECS_PACKAGE,
            )
            runner.outputs[install] = ("", "download failed", 100)
            with self.assertRaisesRegex(StepWarning, "Could not download"):
                InstallMultimediaCodecsStep(runner).execute(context)

        self.assertFalse(context.values["multimedia_codecs_installed"])
        self.assertTrue(
            any(command[-2:] == ("apt-get", "check") for command, _ in runner.commands)
        )

    def test_inconsistent_multimedia_failure_remains_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = CodecInstallRunner()
            context = InstallContext(
                valid_plan(install_multimedia_codecs=True),
                lambda _message: None,
                {
                    "target": target,
                    "chroot_environment_ready": True,
                    "network_online": True,
                    "package_indexes_refreshed": True,
                },
            )
            install = (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "--no-install-recommends",
                "-o",
                "Acquire::Retries=1",
                "-o",
                "Acquire::http::Timeout=15",
                "-o",
                "Acquire::https::Timeout=15",
                "install",
                MULTIMEDIA_CODECS_PACKAGE,
            )
            runner.outputs[install] = ("", "dpkg failed", 100)
            audit = ("chroot", str(target), "dpkg", "--audit")
            runner.outputs[audit] = ("half-configured package", "", 0)
            with self.assertRaisesRegex(RuntimeError, "inconsistent"):
                InstallMultimediaCodecsStep(runner).execute(context)

    def test_refresh_then_upgrade_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = FakeRunner()
            context = context_for(target)
            RefreshPackageIndexesStep(runner).execute(context)
            UpgradeSystemStep(runner).execute(context)
            UpgradeSystemStep(runner).verify(context)

        commands = [item[0] for item in runner.commands]
        self.assertTrue(context.values["package_indexes_refreshed"])
        self.assertTrue(context.values["system_upgrade_downloaded"])
        self.assertTrue(context.values["system_upgraded"])
        self.assertTrue(any(command[-1] == "update" for command in commands))
        upgrades = [command for command in commands if command[-1] == "upgrade"]
        self.assertEqual(len(upgrades), 2)
        self.assertIn("--download-only", upgrades[0])
        self.assertIn("--no-download", upgrades[1])
        self.assertTrue(any(command[-2:] == ("dpkg", "--audit") for command in commands))
        self.assertTrue(any(command[-2:] == ("apt-get", "check") for command in commands))

    def test_offline_refresh_failure_prevents_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = FakeRunner()
            context = context_for(target)
            update_command = (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "-o",
                "Acquire::Retries=1",
                "-o",
                "Acquire::http::Timeout=15",
                "-o",
                "Acquire::https::Timeout=15",
                "update",
            )
            runner.outputs[update_command] = ("", "offline", 100)
            with self.assertRaisesRegex(RuntimeError, "Could not refresh"):
                RefreshPackageIndexesStep(runner).execute(context)
            with self.assertRaisesRegex(StepWarning, "indexes"):
                UpgradeSystemStep(runner).execute(context)

        self.assertFalse(context.values["package_indexes_refreshed"])
        self.assertFalse(context.values["system_upgraded"])
        self.assertFalse(
            any(item[0][-1] == "upgrade" for item in runner.commands)
        )

    def test_offline_mode_skips_all_network_package_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = FakeRunner()
            context = context_for(target)
            context.values["network_online"] = False

            with self.assertRaisesRegex(StepWarning, "offline"):
                RefreshPackageIndexesStep(runner).execute(context)
            with self.assertRaisesRegex(StepWarning, "offline"):
                UpgradeSystemStep(runner).execute(context)

        self.assertEqual(runner.commands, [])
        self.assertFalse(context.values["package_indexes_refreshed"])
        self.assertFalse(context.values["system_upgraded"])

    def test_failed_update_download_never_starts_package_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = FakeRunner()
            context = context_for(target)
            context.values["package_indexes_refreshed"] = True
            command_prefix = (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "-o",
                "Dpkg::Options::=--force-confold",
            )
            download = (
                *command_prefix,
                "-o",
                "Acquire::Retries=1",
                "-o",
                "Acquire::http::Timeout=15",
                "-o",
                "Acquire::https::Timeout=15",
                "--download-only",
                "upgrade",
            )
            runner.outputs[download] = ("", "network lost", 100)

            with self.assertRaisesRegex(StepWarning, "download every"):
                UpgradeSystemStep(runner).execute(context)

        self.assertFalse(context.values["system_upgrade_downloaded"])
        self.assertFalse(context.values["system_upgraded"])
        self.assertFalse(
            any("--no-download" in item[0] for item in runner.commands)
        )

    def test_failed_selected_mirror_restores_original_and_retries(self):
        class SequentialRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.update_attempts = 0

            def run(self, command, **kwargs):
                command = tuple(command)
                self.commands.append((command, kwargs))
                if command[-1] == "update":
                    self.update_attempts += 1
                    return subprocess.CompletedProcess(
                        command, 100 if self.update_attempts == 1 else 0, "", ""
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            source = target / "etc/apt/sources.list.d/ubuntu.sources"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"URIs: http://selected.example/ubuntu/\n")
            context = context_for(target)
            context.values.update(
                {
                    "apt_mirror_source": source,
                    "apt_mirror_original": b"URIs: http://original.example/ubuntu/\n",
                    "apt_mirror_original_mode": 0o644,
                }
            )
            runner = SequentialRunner()
            RefreshPackageIndexesStep(runner).execute(context)
            self.assertEqual(
                source.read_bytes(),
                b"URIs: http://original.example/ubuntu/\n",
            )
            self.assertEqual(runner.update_attempts, 2)
            self.assertTrue(context.values["package_indexes_refreshed"])
            self.assertTrue(context.values["apt_mirror_rolled_back"])

    def test_disabled_updates_run_no_apt_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            context = context_for(Path(directory), updates=False)
            RefreshPackageIndexesStep(runner).execute(context)
            UpgradeSystemStep(runner).execute(context)
        self.assertEqual(runner.commands, [])

    def test_third_party_drivers_use_ubuntu_drivers_no_oem(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            command = target / "usr/bin/ubuntu-drivers"
            command.parent.mkdir(parents=True, exist_ok=True)
            command.touch()
            runner = FakeRunner()
            context = InstallContext(
                valid_plan(install_third_party_drivers=True),
                lambda _message: None,
                {"target": target, "chroot_environment_ready": True},
            )
            InstallThirdPartyDriversStep(runner).execute(context)

        self.assertTrue(context.values["third_party_drivers_installed"])
        self.assertEqual(
            runner.commands[0][0],
            (
                "chroot",
                str(target),
                "ubuntu-drivers",
                "install",
                "--no-oem",
                "--package-list",
                "/run/anduinos-installer-drivers",
            ),
        )

    def test_disabled_third_party_drivers_run_no_command(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            context = InstallContext(
                valid_plan(install_third_party_drivers=False),
                lambda _message: None,
                {
                    "target": Path(directory),
                    "chroot_environment_ready": True,
                },
            )
            InstallThirdPartyDriversStep(runner).execute(context)
        self.assertEqual(runner.commands, [])

    def test_failed_driver_download_warns_when_package_state_is_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            command = target / "usr/bin/ubuntu-drivers"
            command.parent.mkdir(parents=True)
            command.touch()
            runner = FakeRunner()
            context = InstallContext(
                valid_plan(install_third_party_drivers=True),
                lambda _message: None,
                {"target": target, "chroot_environment_ready": True},
            )
            install = (
                "chroot",
                str(target),
                "ubuntu-drivers",
                "install",
                "--no-oem",
                "--package-list",
                "/run/anduinos-installer-drivers",
            )
            runner.outputs[install] = ("", "network lost", 100)

            with self.assertRaisesRegex(StepWarning, "base system"):
                InstallThirdPartyDriversStep(runner).execute(context)

        commands = [item[0] for item in runner.commands]
        self.assertIn(("chroot", str(target), "dpkg", "--audit"), commands)
        self.assertIn(("chroot", str(target), "apt-get", "check"), commands)
        self.assertFalse(context.values["third_party_drivers_installed"])

    def test_failed_driver_install_is_fatal_if_dpkg_is_inconsistent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            command = target / "usr/bin/ubuntu-drivers"
            command.parent.mkdir(parents=True)
            command.touch()
            runner = FakeRunner()
            context = InstallContext(
                valid_plan(install_third_party_drivers=True),
                lambda _message: None,
                {"target": target, "chroot_environment_ready": True},
            )
            install = (
                "chroot",
                str(target),
                "ubuntu-drivers",
                "install",
                "--no-oem",
                "--package-list",
                "/run/anduinos-installer-drivers",
            )
            audit = ("chroot", str(target), "dpkg", "--audit")
            runner.outputs[install] = ("", "install failed", 100)
            runner.outputs[audit] = ("unpacked package remains\n", "", 0)

            with self.assertRaisesRegex(RuntimeError, "inconsistent"):
                InstallThirdPartyDriversStep(runner).execute(context)

        self.assertFalse(context.values["third_party_drivers_installed"])

    def test_offline_third_party_drivers_warn_without_running_command(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            context = InstallContext(
                valid_plan(install_third_party_drivers=True),
                lambda _message: None,
                {
                    "target": Path(directory),
                    "chroot_environment_ready": True,
                    "network_online": False,
                },
            )
            with self.assertRaisesRegex(StepWarning, "offline"):
                InstallThirdPartyDriversStep(runner).execute(context)
        self.assertEqual(runner.commands, [])
