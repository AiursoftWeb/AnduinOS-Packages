import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from languages import INPUT_METHODS, LANGUAGES, InputMethod, input_method
from installer_core.regional_config import (
    ConfigureKeyboardStep,
    InstallInputMethodStep,
)
from installer_core.steps import InstallContext, StepWarning


def plan_for(method_id: str):
    base = valid_plan()
    language = next(
        language
        for language in LANGUAGES
        if language.recommended_input_method == method_id
    )
    return replace(
        base,
        regional=replace(
            base.regional,
            locale=language.locale,
            timezone=language.timezone,
            keyboard=replace(
                base.regional.keyboard,
                layout=language.keyboard,
            ),
            input_method=method_id,
        ),
    )


def context_for(target: Path, plan=None, *, online=True) -> InstallContext:
    return InstallContext(
        plan or valid_plan(),
        lambda _message: None,
        {
            "target": target,
            "chroot_environment_ready": True,
            "network_online": online,
        },
    )


def prepare_apt(target: Path) -> None:
    command = target / "usr/bin/apt-get"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.touch()


def prepare_payload(target: Path, method: InputMethod) -> None:
    for relative in method.required_paths:
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


class ConfigureKeyboardTests(unittest.TestCase):
    def test_configures_and_verifies_keyboard_fully_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = context_for(target, online=False)
            step = ConfigureKeyboardStep()
            step.execute(context)
            step.verify(context)
            content = (target / "etc/default/keyboard").read_text()
        self.assertIn('XKBLAYOUT="us"', content)
        self.assertIn('XKBVARIANT=""', content)


class InstallInputMethodTests(unittest.TestCase):
    def test_non_input_method_locale_runs_no_package_command(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            context = context_for(Path(directory), online=False)
            step = InstallInputMethodStep(runner)
            step.execute(context)
            step.verify(context)
        self.assertEqual(runner.commands, [])
        self.assertIsNone(context.values["input_method_installed"])

    def test_every_input_method_warns_offline_when_payload_is_missing(self):
        for method_id in INPUT_METHODS:
            with self.subTest(method=method_id), tempfile.TemporaryDirectory() as directory:
                runner = FakeRunner()
                context = context_for(
                    Path(directory), plan_for(method_id), online=False
                )
                with self.assertRaisesRegex(StepWarning, "offline"):
                    InstallInputMethodStep(runner).execute(context)
                self.assertEqual(runner.commands, [])
                self.assertFalse(context.values["input_method_installed"])

    def test_every_declared_payload_configures_without_network(self):
        for method in INPUT_METHODS.values():
            with self.subTest(method=method.id), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                prepare_payload(target, method)
                runner = FakeRunner()
                context = context_for(target, plan_for(method.id), online=False)
                step = InstallInputMethodStep(runner)
                step.execute(context)
                step.verify(context)

                self.assertTrue(context.values["input_method_installed"])
                if method.desktop_source is not None:
                    override = (
                        target
                        / "usr/share/glib-2.0/schemas"
                        / "99_anduinos_default_input.gschema.override"
                    ).read_text()
                    self.assertIn(
                        repr(
                            (
                                method.desktop_source.type,
                                method.desktop_source.id,
                            )
                        ),
                        override,
                    )
                self.assertFalse((target / "etc/skel").exists())
                self.assertFalse(
                    any("apt-get" in command for command, _ in runner.commands)
                )

    def test_online_install_uses_only_the_selected_policy_packages(self):
        selected = input_method("mozc")
        assert selected is not None

        class InstallingRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if "install" in command and selected.packages[0] in command:
                    prepare_payload(target, selected)
                return result

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = InstallingRunner()
            context = context_for(target, plan_for(selected.id), online=True)
            step = InstallInputMethodStep(runner)
            step.execute(context)
            step.verify(context)
        commands = [item[0] for item in runner.commands]
        self.assertTrue(any(command[-1] == "update" for command in commands))
        install = next(command for command in commands if "install" in command)
        self.assertEqual(install[-len(selected.packages):], selected.packages)
        self.assertTrue(context.values["package_indexes_refreshed"])
        self.assertTrue(context.values["input_method_installed"])

    def test_failed_download_is_warning_when_package_state_is_clean(self):
        selected = input_method("hangul")
        assert selected is not None
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = FakeRunner()
            context = context_for(target, plan_for(selected.id), online=True)
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
                *selected.packages,
            )
            runner.outputs[install] = ("", "network lost", 100)
            with self.assertRaisesRegex(StepWarning, "Could not download"):
                InstallInputMethodStep(runner).execute(context)
        commands = [item[0] for item in runner.commands]
        self.assertIn(("chroot", str(target), "dpkg", "--audit"), commands)
        self.assertIn(("chroot", str(target), "apt-get", "check"), commands)
