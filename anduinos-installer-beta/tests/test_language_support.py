import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from languages import LANGUAGES, language_pack_packages
from installer_core.language_support import InstallLanguagePacksStep
from installer_core.steps import FailurePolicy, InstallContext, StepWarning


def context_for(target: Path, language_code: str, *, online: bool):
    language = next(item for item in LANGUAGES if item.code == language_code)
    plan = valid_plan()
    plan = replace(
        plan,
        regional=replace(
            plan.regional,
            locale=language.locale,
            timezone=language.timezone,
            keyboard=replace(
                plan.regional.keyboard,
                layout=language.keyboard,
            ),
            input_methods=language.default_input_methods,
        ),
    )
    return InstallContext(
        plan,
        lambda _message: None,
        {
            "target": target,
            "chroot_environment_ready": True,
            "network_online": online,
        },
    )


class PackageRunner(FakeRunner):
    def __init__(self, installed=(), *, fail_install=False):
        super().__init__()
        self.installed = set(installed)
        self.fail_install = fail_install

    def run(self, command, **kwargs):
        command = tuple(command)
        self.commands.append((command, kwargs))
        if "dpkg-query" in command:
            package = command[-1]
            return subprocess.CompletedProcess(
                command,
                0 if package in self.installed else 1,
                "ii " if package in self.installed else "",
                "",
            )
        if "install" in command:
            if self.fail_install:
                return subprocess.CompletedProcess(
                    command, 100, "", "package download failed"
                )
            install_at = command.index("install")
            self.installed.update(command[install_at + 1 :])
        return subprocess.CompletedProcess(command, 0, "", "")


class InstallLanguagePacksTests(unittest.TestCase):
    def test_step_title_describes_its_ensure_semantics(self):
        self.assertEqual(
            InstallLanguagePacksStep.title,
            "Ensure required language packs are installed",
        )

    def test_every_language_has_exact_safe_package_policy(self):
        for language in LANGUAGES:
            with self.subTest(language=language.code):
                packages = language_pack_packages(language)
                self.assertEqual(len(packages), 4)
                self.assertEqual(len(set(packages)), 4)
                self.assertTrue(
                    all(
                        package.startswith("language-pack-")
                        and "*" not in package
                        for package in packages
                    )
                )
        mappings = {
            language.code: language.language_pack_code
            for language in LANGUAGES
        }
        self.assertEqual(mappings["zh_CN"], "zh-hans")
        self.assertEqual(mappings["zh_HK"], "zh-hant")
        self.assertEqual(mappings["zh_TW"], "zh-hant")
        self.assertEqual(mappings["pt_BR"], "pt")

    def test_complete_payload_needs_no_network_or_apt(self):
        language = next(item for item in LANGUAGES if item.code == "ja")
        packages = language_pack_packages(language)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = PackageRunner(packages)
            context = context_for(target, "ja", online=False)
            step = InstallLanguagePacksStep(runner)
            step.execute(context)
            step.verify(context)
        self.assertTrue(context.values["language_packs_installed"])
        self.assertFalse(
            any("apt-get" in command for command, _kwargs in runner.commands)
        )

    def test_missing_offline_payload_is_a_visible_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PackageRunner()
            context = context_for(Path(directory), "zh_CN", online=False)
            step = InstallLanguagePacksStep(runner)
            with self.assertRaisesRegex(StepWarning, "offline"):
                step.execute(context)
        self.assertIs(step.failure_policy, FailurePolicy.WARNING)
        self.assertFalse(context.values["language_packs_installed"])

    def test_online_install_uses_only_selected_exact_packages(self):
        language = next(item for item in LANGUAGES if item.code == "zh_CN")
        packages = language_pack_packages(language)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            apt = target / "usr/bin/apt-get"
            apt.parent.mkdir(parents=True)
            apt.touch()
            runner = PackageRunner()
            context = context_for(target, "zh_CN", online=True)
            step = InstallLanguagePacksStep(runner)
            step.execute(context)
            step.verify(context)
        install = next(
            command
            for command, _kwargs in runner.commands
            if "install" in command
        )
        self.assertEqual(install[-len(packages) :], packages)
        self.assertTrue(context.values["package_indexes_refreshed"])
        self.assertTrue(context.values["language_packs_installed"])

    def test_package_failure_warns_and_allows_installation_to_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            apt = target / "usr/bin/apt-get"
            apt.parent.mkdir(parents=True)
            apt.touch()
            runner = PackageRunner(fail_install=True)
            context = context_for(target, "de", online=True)
            context.values["package_indexes_refreshed"] = True
            with self.assertRaisesRegex(StepWarning, "Could not download"):
                InstallLanguagePacksStep(runner).execute(context)
        self.assertFalse(context.values["language_packs_installed"])


if __name__ == "__main__":
    unittest.main()
