"""Configure the physical keyboard and optional language input method."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from languages import InputMethod, input_method, language_for_locale

from .command import CommandError, CommandRunner
from .software import refresh_package_indexes
from .steps import FailurePolicy, InstallContext, StepSkipped, StepWarning


@dataclass
class ConfigureKeyboardStep:
    """Configure the physical XKB layout without any network access."""

    id: str = "configure-keyboard-layout"
    title: str = "Configure keyboard layout"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        keyboard = context.plan.regional.keyboard
        _write_keyboard(target, keyboard.layout, keyboard.variant)

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        keyboard = context.plan.regional.keyboard
        content = (target / "etc/default/keyboard").read_text(
            encoding="utf-8"
        )
        if f'XKBLAYOUT="{keyboard.layout}"' not in content:
            raise RuntimeError("Keyboard layout verification failed")
        if f'XKBVARIANT="{keyboard.variant}"' not in content:
            raise RuntimeError("Keyboard variant verification failed")

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class InstallInputMethodStep:
    """Install and configure the locale-selected input method when possible."""

    runner: CommandRunner
    id: str = "install-input-method"
    title: str = "Install language input method"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 3
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        method = input_method(context.plan.regional.input_method)
        context.values["input_method_installed"] = False
        _clear_input_method_configuration(target)
        if method is None:
            context.values["input_method_installed"] = None
            context.log("Language input method: not required")
            language = language_for_locale(context.plan.regional.locale)
            if (
                language is not None
                and language.recommended_input_method is None
            ):
                raise StepSkipped(
                    f"An additional input method is not required for "
                    f"{language.native_name}"
                )
            raise StepSkipped(
                "Skipped optional input method because it was not selected"
            )

        context.log(
            f"Selected language input method: {method.display_name} "
            f"({method.id})"
        )
        payload_complete = _input_method_payload_complete(target, method)
        if payload_complete:
            context.log(
                "Input-method payload is already present in the target system; "
                "no package download is needed"
            )
        else:
            if context.values.get("network_online") is False:
                raise StepWarning(
                    f"Skipped {method.display_name} installation "
                    "because the installer is offline; the physical keyboard "
                    "layout is still configured"
                )
            _require_target_command(target, "usr/bin/apt-get")
            context.log(
                "Installing input-method packages in the target system: "
                + ", ".join(method.packages)
            )
            try:
                if not context.values.get("package_indexes_refreshed"):
                    refresh_package_indexes(context, self.runner)
            except CommandError as error:
                raise StepWarning(
                    f"Could not refresh package indexes for the "
                    f"{method.display_name} input method; the physical keyboard "
                    "layout is still configured"
                ) from error
            result = self.runner.run(
                (
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
                    *method.packages,
                ),
                check=False,
                timeout=3600,
            )
            if result.returncode != 0:
                if not _package_state_is_consistent(target, self.runner):
                    raise CommandError(
                        "Input-method installation failed and left an "
                        "inconsistent package state"
                    )
                raise StepWarning(
                    f"Could not download or install {method.display_name}; "
                    "the physical keyboard layout is still "
                    "configured"
                )

        _verify_input_method_payload(target, method)
        context.log(
            f"Input-method payload verified for {method.display_name}"
        )
        if method.desktop_source is not None:
            sources = _input_sources_value(
                method,
                context.plan.regional.keyboard.layout,
            )
            context.log(f"GNOME input-source defaults: sources={sources}")
            context.log(
                "Writing GNOME input-source defaults to "
                "/usr/share/glib-2.0/schemas/"
                "99_anduinos_default_input.gschema.override"
            )
            context.log(
                "This is a system-wide default for new users; per-user dconf "
                "and mru-sources remain managed by GNOME"
            )
        _write_input_method_configuration(
            target,
            method,
            context.plan.regional.keyboard.layout,
        )
        if method.desktop_source is not None:
            self.runner.run(
                (
                    "chroot",
                    str(target),
                    "glib-compile-schemas",
                    "/usr/share/glib-2.0/schemas",
                ),
                timeout=60,
            )
            context.log(
                "Compiled GNOME settings schemas; the input sources will be "
                "available to newly created users"
            )
        context.values["input_method_installed"] = True
        context.log(f"Language input method: {method.id} installed")

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        method = input_method(context.plan.regional.input_method)
        override = _input_override(target)
        if method is None:
            if override.exists():
                raise RuntimeError("Unexpected input-method configuration")
            return
        if context.values.get("input_method_installed") is not True:
            raise RuntimeError("Input-method installation was not recorded")
        _verify_input_method_payload(target, method)
        if method.desktop_source is not None and not override.is_file():
            raise RuntimeError("Input-method configuration is missing")
        if method.desktop_source is None and override.exists():
            raise RuntimeError("Unexpected desktop input-source configuration")

    def cleanup(self, context: InstallContext) -> None:
        return None


def _write_keyboard(target: Path, layout: str, variant: str) -> None:
    default = target / "etc/default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "keyboard").write_text(
        'XKBMODEL="pc105"\n'
        f'XKBLAYOUT="{layout}"\n'
        f'XKBVARIANT="{variant}"\n'
        'XKBOPTIONS=""\n'
        "BACKSPACE=guess\n",
        encoding="utf-8",
    )


def _input_override(target: Path) -> Path:
    return (
        target
        / "usr/share/glib-2.0/schemas"
        / "99_anduinos_default_input.gschema.override"
    )


def _clear_input_method_configuration(target: Path) -> None:
    override = _input_override(target)
    if override.exists() or override.is_symlink():
        override.unlink()


def _write_input_method_configuration(
    target: Path,
    method: InputMethod,
    keyboard_layout: str,
) -> None:
    if method.desktop_source is not None:
        override = _input_override(target)
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(
            "[org.gnome.desktop.input-sources]\n"
            f"sources={_input_sources_value(method, keyboard_layout)}\n",
            encoding="utf-8",
        )


def _input_sources_value(method: InputMethod, keyboard_layout: str) -> str:
    """Return the GSettings value written as the new-user input-source default."""

    if method.desktop_source is None:
        raise ValueError("Input method has no desktop input source")
    return (
        f"[('xkb', '{keyboard_layout}'), "
        f"('{method.desktop_source.type}', '{method.desktop_source.id}')]"
    )


def _input_method_payload_complete(
    target: Path, method: InputMethod
) -> bool:
    return all(
        (target / relative).is_file() for relative in method.required_paths
    )


def _verify_input_method_payload(target: Path, method: InputMethod) -> None:
    missing = [
        str(path)
        for relative in method.required_paths
        if not (path := target / relative).is_file()
    ]
    if missing:
        raise RuntimeError(
            f"{method.display_name} payload is incomplete: "
            + ", ".join(missing)
        )


def _package_state_is_consistent(
    target: Path, runner: CommandRunner
) -> bool:
    audit = runner.run(
        ("chroot", str(target), "dpkg", "--audit"),
        check=False,
        timeout=300,
    )
    dependency_check = runner.run(
        ("chroot", str(target), "apt-get", "check"),
        check=False,
        timeout=600,
    )
    return (
        audit.returncode == 0
        and not audit.stdout.strip()
        and dependency_check.returncode == 0
    )


def _require_target_command(target: Path, relative: str) -> None:
    if not (target / relative).is_file():
        raise RuntimeError(f"Target command is missing: /{relative}")


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not ready")
    return target
