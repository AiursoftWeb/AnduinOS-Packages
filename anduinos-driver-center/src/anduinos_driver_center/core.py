"""Hardware and driver state detection, kept independent from GTK."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
import os
import re
import subprocess
import sys
from typing import Protocol, Sequence

try:
    from anduinos_secureboot import (
        DkmsState,
        SecureBootState,
        inspect_dkms as _inspect_dkms,
        inspect_secure_boot as _inspect_secure_boot,
        normalize_key,
    )
    from anduinos_secureboot.inspect import module_signature
except ModuleNotFoundError:
    _toolkit_src = Path(__file__).resolve().parents[3] / "anduinos-secureboot-toolkit" / "src"
    sys.path.insert(0, str(_toolkit_src))
    from anduinos_secureboot import (
        DkmsState,
        SecureBootState,
        inspect_dkms as _inspect_dkms,
        inspect_secure_boot as _inspect_secure_boot,
        normalize_key,
    )
    from anduinos_secureboot.inspect import module_signature


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
    installed_version: str | None = None
    candidate_version: str | None = None
    update_available: bool = False
    active: bool = False


@dataclass(frozen=True)
class HardwareDevice:
    identifier: str
    vendor: str
    model: str
    modalias: str = ""
    active_driver: str | None = None
    driver_state_known: bool = False
    active_driver_healthy: bool | None = None
    active_driver_version: str | None = None
    active_driver_error: str | None = None
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
class GraphicsScan:
    devices: tuple[HardwareDevice, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def successful(self) -> bool:
        return self.error is None


class XboxStatus(str, Enum):
    NOT_INSTALLED = "not-installed"
    MODULE_MISSING = "module-missing"
    SECURE_BOOT_UNKNOWN = "secure-boot-unknown"
    ENROLLMENT_PENDING = "enrollment-pending"
    TRUST_SETUP_REQUIRED = "trust-setup-required"
    SIGNATURE_MISMATCH = "signature-mismatch"
    LOAD_STATE_UNKNOWN = "load-state-unknown"
    LOADED = "loaded"
    READY = "ready"


@dataclass(frozen=True)
class XboxState:
    status: XboxStatus
    installed: bool
    module_available: bool
    module_loaded: bool
    signature_key: str | None
    signature_matches: bool


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

    @property
    def missing_required_packages(self) -> tuple[PackageState, ...]:
        return tuple(
            package
            for package in self.core_packages + self.driverless_packages
            if not package.installed
        )


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


def package_candidate_version(package: str, runner: Runner) -> str | None:
    result = runner.run(["apt-cache", "policy", package])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        label, separator, value = line.strip().partition(":")
        if separator and label == "Candidate":
            candidate = value.strip()
            return None if candidate == "(none)" else candidate or None
    return None


def package_update_available(
    installed: str | None,
    candidate: str | None,
    runner: Runner,
) -> bool:
    if not installed or not candidate or installed == candidate:
        return False
    result = runner.run(
        ["dpkg", "--compare-versions", installed, "lt", candidate]
    )
    return result.returncode == 0


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
    configuration: Path = Path("/etc/dkms/framework.conf.d/anduinos-sb-sign.conf"),
) -> SecureBootState:
    return _inspect_secure_boot(
        runner, private_key, certificate, configuration=configuration
    )


def _parse_driver_line(line: str, runner: Runner) -> DriverOption | None:
    # ubuntu-drivers emits: "driver : PACKAGE - distro non-free recommended"
    if not line.strip().startswith("driver") or ":" not in line:
        return None
    value = line.split(":", 1)[1].strip()
    package, separator, flags = value.partition(" - ")
    if not separator or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        return None
    words = set(flags.lower().split())
    recommended = "recommended" in words
    installed = package_state(package, runner)
    candidate = (
        package_candidate_version(package, runner) if recommended else None
    )
    return DriverOption(
        package=package,
        description=flags,
        recommended=recommended,
        free="free" in words and "non-free" not in words,
        builtin="builtin" in words,
        installed=installed.installed,
        installed_version=installed.version,
        candidate_version=candidate,
        update_available=package_update_available(
            installed.version, candidate, runner
        ),
    )


def _active_graphics_driver(
    identifier: str, runner: Runner
) -> tuple[bool, str | None]:
    """Return the kernel driver bound to the PCI device in an ubuntu-drivers path."""
    matches = list(re.finditer(
        r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:"
        r"[0-9a-fA-F]{2}\.[0-7](?=$|/)",
        identifier,
    ))
    if not matches:
        return False, None
    slot = matches[-1].group(0)
    result = runner.run(["lspci", "-k", "-s", slot])
    if result.returncode != 0:
        return False, None
    for line in result.stdout.splitlines():
        if "Kernel driver in use:" in line:
            driver = line.split(":", 1)[1].strip()
            return True, driver or None
    return True, None


def _active_driver_health(
    active_driver: str | None,
    driver_state_known: bool,
    runner: Runner,
) -> tuple[bool | None, str | None, str | None]:
    """Verify the bound graphics driver without loading or switching modules."""
    if not driver_state_known:
        return None, None, "Kernel driver binding could not be determined"
    if not active_driver:
        return False, None, "No kernel driver is bound to this device"
    if not active_driver.lower().replace("_", "-").startswith("nvidia"):
        return True, None, None

    module = runner.run(["modinfo", "-F", "version", "nvidia"])
    module_version = module.stdout.strip().splitlines()[0] if (
        module.returncode == 0 and module.stdout.strip()
    ) else None
    if not module_version:
        detail = (module.stderr or module.stdout).strip()
        return False, None, detail or "NVIDIA kernel module metadata is unavailable"

    userspace = runner.run(
        [
            "nvidia-smi",
            "--query-gpu=driver_version",
            "--format=csv,noheader",
        ],
        timeout=10,
    )
    userspace_version = userspace.stdout.strip().splitlines()[0] if (
        userspace.returncode == 0 and userspace.stdout.strip()
    ) else None
    if not userspace_version:
        detail = (userspace.stderr or userspace.stdout).strip()
        return False, module_version, detail or "nvidia-smi could not communicate with the driver"
    if userspace_version != module_version:
        return (
            False,
            module_version,
            f"NVIDIA version mismatch: kernel {module_version}, userspace {userspace_version}",
        )
    return True, module_version, None


def _options_with_active_driver(
    options: list[DriverOption],
    active_driver: str | None,
    active_driver_version: str | None = None,
) -> tuple[DriverOption, ...]:
    """Map a bound kernel driver to one installed ubuntu-drivers package."""
    if not active_driver:
        return tuple(options)

    normalized = active_driver.lower().replace("_", "-")
    if normalized.startswith("nvidia"):
        candidates = [
            option
            for option in options
            if option.installed and option.package.startswith("nvidia-driver-")
        ]
        if active_driver_version:
            series = active_driver_version.split(".", 1)[0]
            matching_series = [
                option
                for option in candidates
                if option.package.startswith(f"nvidia-driver-{series}-")
                or option.package == f"nvidia-driver-{series}"
            ]
            if matching_series:
                candidates = matching_series
    else:
        package = f"xserver-xorg-video-{normalized}"
        candidates = [option for option in options if option.package == package]

    # Package presence cannot distinguish two co-installed NVIDIA variants.
    # Refuse to guess which metapackage owns the running module.
    if len(candidates) != 1:
        return tuple(options)
    active_package = candidates[0].package
    return tuple(
        replace(option, active=option.package == active_package)
        for option in options
    )


def parse_ubuntu_driver_devices(output: str, runner: Runner) -> list[HardwareDevice]:
    devices: list[HardwareDevice] = []
    block: dict[str, str] = {}
    options: list[DriverOption] = []

    def finish() -> None:
        nonlocal block, options
        if block or options:
            identifier = block.get("path") or block.get("modalias") or f"device-{len(devices)}"
            driver_state_known, active_driver = _active_graphics_driver(
                identifier, runner
            )
            driver_healthy, driver_version, driver_error = _active_driver_health(
                active_driver, driver_state_known, runner
            )
            devices.append(
                HardwareDevice(
                    identifier=identifier,
                    vendor=block.get("vendor", ""),
                    model=block.get("model", ""),
                    modalias=block.get("modalias", ""),
                    active_driver=active_driver,
                    driver_state_known=driver_state_known,
                    active_driver_healthy=driver_healthy,
                    active_driver_version=driver_version,
                    active_driver_error=driver_error,
                    options=_options_with_active_driver(
                        options, active_driver, driver_version
                    ),
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


def graphics_scan(runner: Runner | None = None) -> GraphicsScan:
    runner = runner or SubprocessRunner()
    result = runner.run(["ubuntu-drivers", "devices"], timeout=30)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return GraphicsScan(
            error=detail or "ubuntu-drivers could not inspect this hardware"
        )
    return GraphicsScan(
        devices=tuple(parse_ubuntu_driver_devices(result.stdout, runner))
    )


def graphics_devices(runner: Runner | None = None) -> list[HardwareDevice]:
    """Compatibility wrapper for callers that only need successfully found devices."""
    return list(graphics_scan(runner).devices)


def xbox_state(
    secure_boot: SecureBootState,
    runner: Runner | None = None,
) -> XboxState:
    runner = runner or SubprocessRunner()
    installed = package_is_installed("anduinos-xbox-controller-driver", runner)
    module = runner.run(["modinfo", "-F", "filename", "hid-xpadneo"])
    module_available = bool(
        installed and module.returncode == 0 and module.stdout.strip()
    )
    signature = (
        module_signature("hid-xpadneo", runner) if module_available else None
    )
    modules = runner.run(["lsmod"])
    load_state_known = modules.returncode == 0
    loaded = load_state_known and any(
        line.split(maxsplit=1)[0] in {"hid_xpadneo", "xpadneo"}
        for line in modules.stdout.splitlines()
        if line.strip()
    )
    matches = bool(
        signature and secure_boot.certificate_serial
        and signature == secure_boot.certificate_serial
    )
    if not installed:
        status = XboxStatus.NOT_INSTALLED
    elif not module_available:
        status = XboxStatus.MODULE_MISSING
    elif not secure_boot.state_known:
        status = XboxStatus.SECURE_BOOT_UNKNOWN
    elif secure_boot.enabled and secure_boot.enrollment_pending:
        status = XboxStatus.ENROLLMENT_PENDING
    elif secure_boot.enabled and not secure_boot.enrolled:
        status = XboxStatus.TRUST_SETUP_REQUIRED
    elif secure_boot.enabled and not matches:
        status = XboxStatus.SIGNATURE_MISMATCH
    elif not load_state_known:
        status = XboxStatus.LOAD_STATE_UNKNOWN
    elif loaded:
        status = XboxStatus.LOADED
    else:
        status = XboxStatus.READY
    return XboxState(
        status,
        installed,
        module_available,
        loaded,
        signature,
        matches,
    )


def dkms_state(
    secure_boot: SecureBootState,
    runner: Runner | None = None,
    module_directory: Path | None = None,
) -> DkmsState:
    return _inspect_dkms(secure_boot, runner, module_directory)


def scan_system(
    runner: Runner | None = None,
) -> tuple[
    GraphicsScan,
    SecureBootState,
    XboxState,
    DkmsState,
    AudioState,
    PrintingState,
]:
    runner = runner or SubprocessRunner()
    secure_boot = secure_boot_state(runner)
    return (
        graphics_scan(runner),
        secure_boot,
        xbox_state(secure_boot, runner),
        dkms_state(secure_boot, runner),
        audio_state(runner),
        printing_state(runner),
    )
