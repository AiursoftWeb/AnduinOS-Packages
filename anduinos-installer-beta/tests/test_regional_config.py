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
from installer_core.steps import (
    InstallContext,
    StepRunner,
    StepStatus,
    StepWarning,
)


def plan_for(*method_ids: str):
    base = valid_plan()
    language = next(
        language
        for language in LANGUAGES
        if all(
            method_id in language.recommended_input_methods
            for method_id in method_ids
        )
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
            input_methods=method_ids,
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

    def test_configures_a_catalogued_keyboard_variant(self):
        base = valid_plan()
        plan = replace(
            base,
            regional=replace(
                base.regional,
                keyboard=replace(
                    base.regional.keyboard,
                    layout="us",
                    variant="intl",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = context_for(target, plan, online=False)
            step = ConfigureKeyboardStep()
            step.execute(context)
            step.verify(context)
            content = (target / "etc/default/keyboard").read_text()
        self.assertIn('XKBLAYOUT="us"', content)
        self.assertIn('XKBVARIANT="intl"', content)


class InstallInputMethodTests(unittest.TestCase):
    def test_non_input_method_locale_is_skipped_and_clears_live_default(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            override = (
                target
                / "usr/share/glib-2.0/schemas"
                / "99_anduinos_default_input.gschema.override"
            )
            override.parent.mkdir(parents=True)
            override.write_text("copied live-session default")
            runner = FakeRunner()
            context = context_for(target, online=False)
            step = InstallInputMethodStep(runner)
            result = StepRunner([step]).run(context)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.results[0].status, StepStatus.SKIPPED)
        self.assertIn("not required", result.results[0].message)
        self.assertEqual(runner.commands, [])
        self.assertIsNone(context.values["input_methods_installed"])
        self.assertFalse(override.exists())

    def test_unselected_recommended_input_method_is_skipped(self):
        plan = plan_for("rime")
        plan = replace(
            plan,
            regional=replace(plan.regional, input_methods=()),
        )
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            context = context_for(Path(directory), plan, online=True)
            result = StepRunner(
                [InstallInputMethodStep(runner)]
            ).run(context)
        self.assertEqual(result.results[0].status, StepStatus.SKIPPED)
        self.assertIn("not selected", result.results[0].message)
        self.assertEqual(runner.commands, [])

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
                self.assertFalse(context.values["input_methods_installed"])

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

                self.assertTrue(context.values["input_methods_installed"])
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

    def test_logs_the_gnome_input_source_value_and_destination(self):
        selected = input_method("rime")
        assert selected is not None
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_payload(target, selected)
            messages = []
            context = InstallContext(
                plan_for(selected.id),
                messages.append,
                {
                    "target": target,
                    "chroot_environment_ready": True,
                    "network_online": False,
                },
            )
            InstallInputMethodStep(FakeRunner()).execute(context)

        self.assertIn(
            "GNOME input-source defaults: "
            "sources=[('xkb', 'us'), ('ibus', 'rime')]",
            messages,
        )
        self.assertIn(
            "Writing GNOME input-source defaults to "
            "/usr/share/glib-2.0/schemas/"
            "99_anduinos_default_input.gschema.override",
            messages,
        )
        self.assertIn(
            "This is a system-wide default for new users; per-user dconf "
            "and mru-sources remain managed by GNOME",
            messages,
        )

    def test_gnome_input_source_preserves_the_keyboard_variant(self):
        selected = input_method("rime")
        assert selected is not None
        plan = plan_for(selected.id)
        plan = replace(
            plan,
            regional=replace(
                plan.regional,
                keyboard=replace(
                    plan.regional.keyboard,
                    layout="us",
                    variant="intl",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_payload(target, selected)
            context = context_for(target, plan, online=False)
            step = InstallInputMethodStep(FakeRunner())
            step.execute(context)
            step.verify(context)
            override = (
                target
                / "usr/share/glib-2.0/schemas"
                / "99_anduinos_default_input.gschema.override"
            ).read_text(encoding="utf-8")

        self.assertIn("('xkb', 'us+intl')", override)

    def test_multiple_selected_methods_are_all_registered_in_policy_order(self):
        selected = tuple(
            method
            for method_id in ("rime", "wubi")
            if (method := input_method(method_id)) is not None
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for method in selected:
                prepare_payload(target, method)
            context = context_for(
                target, plan_for("rime", "wubi"), online=False
            )
            step = InstallInputMethodStep(FakeRunner())
            step.execute(context)
            step.verify(context)
            override = (
                target
                / "usr/share/glib-2.0/schemas"
                / "99_anduinos_default_input.gschema.override"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            override,
            "[org.gnome.desktop.input-sources]\n"
            "sources=[('xkb', 'us'), ('ibus', 'rime'), "
            "('ibus', 'table:wubi-jidian86')]\n",
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
        self.assertTrue(context.values["input_methods_installed"])

    def test_online_install_merges_packages_for_every_selected_method(self):
        selected = tuple(
            method
            for method_id in ("rime", "wubi")
            if (method := input_method(method_id)) is not None
        )
        expected_packages = tuple(
            dict.fromkeys(
                package
                for method in selected
                for package in method.packages
            )
        )

        class InstallingRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if "install" in command:
                    for method in selected:
                        prepare_payload(target, method)
                return result

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            runner = InstallingRunner()
            context = context_for(
                target, plan_for("rime", "wubi"), online=True
            )
            step = InstallInputMethodStep(runner)
            step.execute(context)
            step.verify(context)

        commands = [item[0] for item in runner.commands]
        install = next(command for command in commands if "install" in command)
        self.assertEqual(install[-len(expected_packages):], expected_packages)
        self.assertEqual(len(expected_packages), len(set(expected_packages)))

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
