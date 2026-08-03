"""Hardware and driver state detection, kept independent from GTK."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import subprocess
from typing import Protocol, Sequence


MOK_PRIVATE_KEY = Path("/var/lib/shim-signed/mok/MOK.priv")
MOK_CERTIFICATE = Path("/var/lib/shim-signed/mok/MOK.der")
SOF_PACKAGE = "firmware-sof-anduinos"
UCM_PACKAGE = "alsa-ucm-conf-anduinos"
PRINTING_CORE_PACKAGES = ("cups", "cups-client")
PRINTING_DRIVERLESS_PACKAGES = (
    "cups-core-drivers",
    "cups-filters",
    "cups-filters-core-drivers",
    "cups-ipp-utils",
)
PRINTING_DISCOVERY_PACKAGES = ("cups-browsed", "avahi-daemon")
PRINTING_OPTIONAL_PACKAGES = (
    "ipp-usb",
    "cups-pk-helper",
    "printer-driver-all",
    "sane-airscan",
)


class Runner(Protocol):
    def run(self, command: Sequence[str], timeout: int = 10) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    def run(self, command: Sequence[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
        try:
            environment = os.environ.copy()
            environment["LC_ALL"] = "C"
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))


@dataclass(frozen=True)
class DriverOption:
    package: str
    description: str
    recommended: bool = False
    free: bool = False
    builtin: bool = False
    installed: bool = False


@dataclass(frozen=True)
class HardwareDevice:
    identifier: str
    vendor: str
    model: str
    modalias: str = ""
    options: tuple[DriverOption, ...] = field(default_factory=tuple)

    @property
    def title(self) -> str:
        model = self.model.strip()
        vendor = self.vendor.replace(" Corporation", "").strip()
        bracketed = re.findall(r"\[([^]]+)]", model)
        if bracketed:
            model = bracketed[-1].strip()
        if vendor and model and not model.lower().startswith(vendor.lower()):
            return f"{vendor} {model}"
        return model or vendor or "Graphics device"


@dataclass(frozen=True)
class SecureBootState:
    enabled: bool
    key_present: bool
    certificate_present: bool
    enrolled: bool
    certificate_serial: str | None

    @property
    def ready(self) -> bool:
        return not self.enabled or (
            self.key_present and self.certificate_present and self.enrolled
        )


@dataclass(frozen=True)
class XboxState:
    installed: bool
    module_loaded: bool
    signature_key: str | None
    signature_matches: bool
    blocked_by_secure_boot: bool


@dataclass(frozen=True)
class DkmsState:
    modules: tuple[str, ...]
    trusted_modules: tuple[str, ...]
    untrusted_modules: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.untrusted_modules


@dataclass(frozen=True)
class PackageState:
    name: str
    installed: bool
    version: str | None


@dataclass(frozen=True)
class AudioState:
    sof_package: PackageState
    ucm_package: PackageState
    firmware_present: bool
    ucm_profiles_present: bool
    sof_modules: tuple[str, ...]
    active_drivers: tuple[str, ...]

    @property
    def packages_installed(self) -> bool:
        return self.sof_package.installed and self.ucm_package.installed

    @property
    def ready(self) -> bool:
        return (
            self.packages_installed
            and self.firmware_present
            and self.ucm_profiles_present
        )


@dataclass(frozen=True)
class PrintingState:
    service_running: bool
    startup_enabled: bool
    printers: tuple[str, ...]
    disabled_printers: tuple[str, ...]
    default_printer: str | None
    core_packages: tuple[PackageState, ...]
    driverless_packages: tuple[PackageState, ...]
    discovery_packages: tuple[PackageState, ...]
    optional_packages: tuple[PackageState, ...]

    @property
    def core_ready(self) -> bool:
        return self.service_running and all(
            package.installed for package in self.core_packages
        )

    @property
    def queues_ready(self) -> bool:
        return self.service_running and not self.disabled_printers

    @property
    def packages(self) -> tuple[PackageState, ...]:
        return (
            self.core_packages
            + self.driverless_packages
            + self.discovery_packages
            + self.optional_packages
        )

    @property
    def missing_packages(self) -> tuple[PackageState, ...]:
        return tuple(package for package in self.packages if not package.installed)


def normalize_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9a-f]", "", value.lower())
    return normalized or None


def package_is_installed(package: str, runner: Runner) -> bool:
    result = runner.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package])
    return result.returncode == 0 and result.stdout.startswith("ii ")


def package_state(package: str, runner: Runner) -> PackageState:
    installed = package_is_installed(package, runner)
    if not installed:
        return PackageState(package, False, None)
    result = runner.run(["dpkg-query", "-W", "-f=${Version}", package])
    version = result.stdout.strip() if result.returncode == 0 else None
    return PackageState(package, True, version or None)


def _directory_contains_files(directory: Path, suffix: str | None = None) -> bool:
    if not directory.is_dir():
        return False
    try:
        return any(
            path.is_file() and (suffix is None or path.name.endswith(suffix))
            for path in directory.rglob("*")
        )
    except OSError:
        return False


def _active_audio_drivers(output: str) -> tuple[str, ...]:
    drivers: set[str] = set()
    audio_device = False
    for line in output.splitlines():
        if line and not line[0].isspace():
            lowered = line.lower()
            audio_device = "audio device" in lowered or "multimedia audio controller" in lowered
            continue
        if audio_device and "Kernel driver in use:" in line:
            driver = line.split(":", 1)[1].strip()
            if driver:
                drivers.add(driver)
    return tuple(sorted(drivers))


def audio_state(
    runner: Runner | None = None,
    firmware_directories: Sequence[Path] | None = None,
    ucm_directory: Path = Path("/usr/share/alsa/ucm2"),
) -> AudioState:
    runner = runner or SubprocessRunner()
    firmware_directories = firmware_directories or (
        Path("/lib/firmware/intel/sof"),
        Path("/lib/firmware/intel/sof-ipc4"),
    )
    modules = runner.run(["lsmod"])
    sof_modules = tuple(
        sorted(
            {
                line.split(maxsplit=1)[0]
                for line in modules.stdout.splitlines()
                if line.strip() and line.split(maxsplit=1)[0].startswith("snd_sof")
            }
        )
    ) if modules.returncode == 0 else ()
    pci = runner.run(["lspci", "-nnk"])
    active_drivers = _active_audio_drivers(pci.stdout) if pci.returncode == 0 else ()
    return AudioState(
        sof_package=package_state(SOF_PACKAGE, runner),
        ucm_package=package_state(UCM_PACKAGE, runner),
        firmware_present=any(
            _directory_contains_files(directory) for directory in firmware_directories
        ),
        ucm_profiles_present=_directory_contains_files(ucm_directory, ".conf"),
        sof_modules=sof_modules,
        active_drivers=active_drivers,
    )


def _printer_queues(output: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    printers: list[str] = []
    disabled: list[str] = []
    for line in output.splitlines():
        match = re.match(r"^printer\s+(\S+)\s+(.+)$", line.strip())
        if not match:
            continue
        name, status = match.groups()
        printers.append(name)
        if " disabled " in f" {status.lower()} ":
            disabled.append(name)
    return tuple(printers), tuple(disabled)


def printing_state(runner: Runner | None = None) -> PrintingState:
    runner = runner or SubprocessRunner()
    service = runner.run(["systemctl", "is-active", "cups.service"])
    socket = runner.run(["systemctl", "is-active", "cups.socket"])
    service_running = (
        service.returncode == 0 and service.stdout.strip() == "active"
    ) or (socket.returncode == 0 and socket.stdout.strip() == "active")

    service_enabled = runner.run(["systemctl", "is-enabled", "cups.service"])
    socket_enabled = runner.run(["systemctl", "is-enabled", "cups.socket"])
    enabled_states = {"enabled", "static", "indirect"}
    startup_enabled = (
        service_enabled.returncode == 0
        and service_enabled.stdout.strip() in enabled_states
    ) or (
        socket_enabled.returncode == 0
        and socket_enabled.stdout.strip() in enabled_states
    )

    queues = runner.run(["lpstat", "-p"])
    printers, disabled = (
        _printer_queues(queues.stdout) if queues.returncode == 0 else ((), ())
    )
    default_result = runner.run(["lpstat", "-d"])
    default_printer = None
    if default_result.returncode == 0 and ":" in default_result.stdout:
        candidate = default_result.stdout.split(":", 1)[1].strip()
        default_printer = candidate or None

    def states(packages: Sequence[str]) -> tuple[PackageState, ...]:
        return tuple(package_state(package, runner) for package in packages)

    return PrintingState(
        service_running=service_running,
        startup_enabled=startup_enabled,
        printers=printers,
        disabled_printers=disabled,
        default_printer=default_printer,
        core_packages=states(PRINTING_CORE_PACKAGES),
        driverless_packages=states(PRINTING_DRIVERLESS_PACKAGES),
        discovery_packages=states(PRINTING_DISCOVERY_PACKAGES),
        optional_packages=states(PRINTING_OPTIONAL_PACKAGES),
    )


def secure_boot_state(
    runner: Runner | None = None,
    private_key: Path = MOK_PRIVATE_KEY,
    certificate: Path = MOK_CERTIFICATE,
) -> SecureBootState:
    runner = runner or SubprocessRunner()
    state = runner.run(["mokutil", "--sb-state"])
    enabled = state.returncode == 0 and "secureboot enabled" in state.stdout.lower()
    key_present = private_key.is_file()
    certificate_present = certificate.is_file()
    enrolled = False
    serial = None

    if certificate_present:
        test = runner.run(["mokutil", "--test-key", str(certificate)])
        enrolled = test.returncode == 0 or "already enrolled" in (
            test.stdout + test.stderr
        ).lower()
        cert = runner.run(
            ["openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-serial"]
        )
        if cert.returncode == 0 and "=" in cert.stdout:
            serial = normalize_key(cert.stdout.strip().split("=", 1)[1])

    return SecureBootState(enabled, key_present, certificate_present, enrolled, serial)


def _parse_driver_line(line: str, runner: Runner) -> DriverOption | None:
    # ubuntu-drivers emits: "driver : PACKAGE - distro non-free recommended"
    if not line.strip().startswith("driver") or ":" not in line:
        return None
    value = line.split(":", 1)[1].strip()
    package, separator, flags = value.partition(" - ")
    if not separator or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        return None
    words = set(flags.lower().split())
    return DriverOption(
        package=package,
        description=flags,
        recommended="recommended" in words,
        free="free" in words and "non-free" not in words,
        builtin="builtin" in words,
        installed=package_is_installed(package, runner),
    )


def parse_ubuntu_driver_devices(output: str, runner: Runner) -> list[HardwareDevice]:
    devices: list[HardwareDevice] = []
    block: dict[str, str] = {}
    options: list[DriverOption] = []

    def finish() -> None:
        nonlocal block, options
        if block or options:
            identifier = block.get("path") or block.get("modalias") or f"device-{len(devices)}"
            devices.append(
                HardwareDevice(
                    identifier=identifier,
                    vendor=block.get("vendor", ""),
                    model=block.get("model", ""),
                    modalias=block.get("modalias", ""),
                    options=tuple(options),
                )
            )
        block = {}
        options = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("==") and line.endswith("=="):
            finish()
            block["path"] = line.strip("= ")
            continue
        option = _parse_driver_line(line, runner)
        if option:
            options.append(option)
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            if key.strip() in {"vendor", "model", "modalias"}:
                block[key.strip()] = value.strip()
    finish()
    return [device for device in devices if device.options]


def graphics_devices(runner: Runner | None = None) -> list[HardwareDevice]:
    runner = runner or SubprocessRunner()
    result = runner.run(["ubuntu-drivers", "devices"], timeout=30)
    if result.returncode != 0:
        return []
    return parse_ubuntu_driver_devices(result.stdout, runner)


def module_signature(module: str, runner: Runner) -> str | None:
    result = runner.run(["modinfo", module])
    if result.returncode:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("sig_key:"):
            return normalize_key(line.split(":", 1)[1])
    return None


def xbox_state(
    secure_boot: SecureBootState,
    runner: Runner | None = None,
) -> XboxState:
    runner = runner or SubprocessRunner()
    installed = package_is_installed("anduinos-xbox-controller-driver", runner)
    signature = module_signature("hid-xpadneo", runner) if installed else None
    modules = runner.run(["lsmod"])
    loaded = modules.returncode == 0 and any(
        line.split(maxsplit=1)[0] in {"hid_xpadneo", "xpadneo"}
        for line in modules.stdout.splitlines()
        if line.strip()
    )
    matches = bool(
        signature and secure_boot.certificate_serial
        and signature == secure_boot.certificate_serial
    )
    blocked = bool(
        installed and secure_boot.enabled
        and (not secure_boot.enrolled or not matches)
    )
    return XboxState(installed, loaded, signature, matches, blocked)


def dkms_state(
    secure_boot: SecureBootState,
    runner: Runner | None = None,
    module_directory: Path | None = None,
) -> DkmsState:
    runner = runner or SubprocessRunner()
    module_directory = module_directory or Path(
        "/lib/modules", os.uname().release, "updates/dkms"
    )
    modules: list[str] = []
    trusted: list[str] = []
    untrusted: list[str] = []
    if not module_directory.is_dir():
        return DkmsState((), (), ())
    for path in sorted(module_directory.iterdir()):
        if not any(path.name.endswith(suffix) for suffix in (".ko", ".ko.xz", ".ko.zst")):
            continue
        modules.append(path.name)
        signature = module_signature(str(path), runner)
        if not secure_boot.enabled or (
            signature and secure_boot.certificate_serial
            and signature == secure_boot.certificate_serial
            and secure_boot.enrolled
        ):
            trusted.append(path.name)
        else:
            untrusted.append(path.name)
    return DkmsState(tuple(modules), tuple(trusted), tuple(untrusted))


def scan_system(runner: Runner | None = None) -> tuple[list[HardwareDevice], SecureBootState, XboxState, DkmsState, AudioState, PrintingState]:
    runner = runner or SubprocessRunner()
    secure_boot = secure_boot_state(runner)
    return (
        graphics_devices(runner),
        secure_boot,
        xbox_state(secure_boot, runner),
        dkms_state(secure_boot, runner),
        audio_state(runner),
        printing_state(runner),
    )
