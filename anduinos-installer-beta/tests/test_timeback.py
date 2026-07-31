import subprocess
import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.command import CommandError
from installer_core.steps import InstallContext, StepWarning
from installer_core.timeback import (
    TIMEBACK_PACKAGE,
    ProvisionTimebackMachineStep,
)


class StatefulPackageRunner(FakeRunner):
    def __init__(
        self,
        *,
        installed: bool = False,
        install_returncode: int = 0,
        inconsistent: bool = False,
    ):
        super().__init__()
        self.installed = installed
        self.install_returncode = install_returncode
        self.inconsistent = inconsistent

    def run(self, command, **kwargs):
        command = tuple(command)
        self.commands.append((command, kwargs))
        if "dpkg-query" in command:
            return subprocess.CompletedProcess(
                command,
                0 if self.installed else 1,
                "ii \n" if self.installed else "",
                "",
            )
        if command[-2:] == ("install", TIMEBACK_PACKAGE):
            if self.install_returncode == 0:
                self.installed = True
            return subprocess.CompletedProcess(
                command,
                self.install_returncode,
                "",
                "download failed" if self.install_returncode else "",
            )
        if command[-2:] == ("dpkg", "--audit"):
            return subprocess.CompletedProcess(
                command,
                1 if self.inconsistent else 0,
                "broken-package\n" if self.inconsistent else "",
                "",
            )
        if command[-2:] == ("apt-get", "check"):
            return subprocess.CompletedProcess(
                command,
                100 if self.inconsistent else 0,
                "",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")


def context_for(
    target: Path,
    *,
    online: bool,
    media_payload: bool = False,
) -> InstallContext:
    apt_get = target / "usr/bin/apt-get"
    apt_get.parent.mkdir(parents=True, exist_ok=True)
    apt_get.touch()
    return InstallContext(
        valid_plan(),
        lambda _message: None,
        {
            "target": target,
            "chroot_environment_ready": True,
            "network_online": online,
            "timeback_payload_in_live_image": media_payload,
        },
    )


class ProvisionTimebackMachineTests(unittest.TestCase):
    def test_retains_installation_media_payload_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = StatefulPackageRunner(installed=True)
            context = context_for(
                target,
                online=False,
                media_payload=True,
            )
            step = ProvisionTimebackMachineStep(runner)
            step.execute(context)
            step.verify(context)

        self.assertTrue(context.values["timeback_machine_installed"])
        self.assertEqual(
            context.values["timeback_machine_source"],
            "installation-media",
        )
        self.assertFalse(
            any(command[-1] == "update" for command, _ in runner.commands)
        )
        self.assertFalse(
            any(
                command[-2:] == ("install", TIMEBACK_PACKAGE)
                for command, _ in runner.commands
            )
        )

    def test_old_online_iso_refreshes_indexes_and_installs_package(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = StatefulPackageRunner()
            context = context_for(target, online=True)
            step = ProvisionTimebackMachineStep(runner)
            step.execute(context)
            step.verify(context)

        commands = [command for command, _ in runner.commands]
        self.assertTrue(context.values["package_indexes_refreshed"])
        self.assertTrue(context.values["timeback_machine_installed"])
        self.assertEqual(
            context.values["timeback_machine_source"],
            "repository",
        )
        self.assertTrue(any(command[-1] == "update" for command in commands))
        install = next(
            command
            for command in commands
            if command[-2:] == ("install", TIMEBACK_PACKAGE)
        )
        self.assertIn("--no-install-recommends", install)

    def test_old_offline_iso_warns_without_running_apt(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = StatefulPackageRunner()
            context = context_for(target, online=False)
            with self.assertRaisesRegex(StepWarning, "offline"):
                ProvisionTimebackMachineStep(runner).execute(context)

        self.assertFalse(context.values["timeback_machine_installed"])
        self.assertFalse(
            any(
                command[-1] == "update"
                or command[-2:] == ("install", TIMEBACK_PACKAGE)
                for command, _ in runner.commands
            )
        )

    def test_clean_download_failure_is_only_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = StatefulPackageRunner(install_returncode=100)
            context = context_for(target, online=True)
            with self.assertRaisesRegex(StepWarning, "remains usable"):
                ProvisionTimebackMachineStep(runner).execute(context)

        commands = [command for command, _ in runner.commands]
        self.assertIn(("chroot", str(target), "dpkg", "--audit"), commands)
        self.assertIn(("chroot", str(target), "apt-get", "check"), commands)

    def test_inconsistent_package_failure_remains_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = StatefulPackageRunner(
                install_returncode=100,
                inconsistent=True,
            )
            context = context_for(target, online=True)
            with self.assertRaisesRegex(
                CommandError,
                "inconsistent package state",
            ):
                ProvisionTimebackMachineStep(runner).execute(context)


if __name__ == "__main__":
    unittest.main()
