from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "anduinos-secureboot-toolkit"
        / "src"
    ),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_driver_center.core import (  # noqa: E402
    audio_state,
    normalize_key,
    dkms_state,
    graphics_scan,
    parse_ubuntu_driver_devices,
    printing_state,
    secure_boot_state,
    XboxStatus,
    xbox_state,
)
from anduinos_secureboot import SecureBootStatus  # noqa: E402


class FakeRunner:
    def __init__(self, responses=None, installed=(), versions=None):
        self.responses = responses or {}
        self.installed = set(installed)
        self.versions = versions or {}

    def run(self, command, timeout=10):
        command = tuple(command)
        if command[:3] == ("dpkg-query", "-W", "-f=${db:Status-Abbrev}"):
            package = command[3]
            return subprocess.CompletedProcess(
                command,
                0 if package in self.installed else 1,
                "ii " if package in self.installed else "",
                "",
            )
        if command[:3] == ("dpkg-query", "-W", "-f=${Version}"):
            package = command[3]
            version = self.versions.get(package)
            return subprocess.CompletedProcess(
                command, 0 if version else 1, version or "", ""
            )
        return self.responses.get(
            command, subprocess.CompletedProcess(command, 1, "", "")
        )


class CoreTests(unittest.TestCase):
    def test_normalizes_certificate_and_module_key_formats(self):
        self.assertEqual(normalize_key("AB:12 cd 34"), "ab12cd34")
        self.assertIsNone(normalize_key("---"))

    def test_parses_ubuntu_drivers_device_and_recommendation(self):
        output = """== /sys/devices/pci0000:00/0000:01:00.0 ==
modalias : pci:v000010DEd00002820
vendor   : NVIDIA Corporation
model    : AD107M [GeForce RTX 4060 Max-Q]
driver   : nvidia-driver-590 - distro non-free recommended
driver   : xserver-xorg-video-nouveau - distro free builtin
"""
        devices = parse_ubuntu_driver_devices(
            output, FakeRunner(installed={"nvidia-driver-590"})
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].vendor, "NVIDIA Corporation")
        self.assertEqual(devices[0].model, "AD107M [GeForce RTX 4060 Max-Q]")
        self.assertEqual(devices[0].title, "NVIDIA GeForce RTX 4060 Max-Q")
        self.assertTrue(devices[0].options[0].recommended)
        self.assertTrue(devices[0].options[0].installed)
        self.assertTrue(devices[0].options[1].free)
        self.assertTrue(devices[0].options[1].builtin)

    def test_graphics_scan_distinguishes_no_devices_from_probe_failure(self):
        no_devices = graphics_scan(
            FakeRunner(
                {
                    ("ubuntu-drivers", "devices"): subprocess.CompletedProcess(
                        [], 0, "", ""
                    )
                }
            )
        )
        failed = graphics_scan(
            FakeRunner(
                {
                    ("ubuntu-drivers", "devices"): subprocess.CompletedProcess(
                        [], 1, "", "hardware database unavailable"
                    )
                }
            )
        )
        self.assertTrue(no_devices.successful)
        self.assertEqual(no_devices.devices, ())
        self.assertIsNone(no_devices.error)
        self.assertFalse(failed.successful)
        self.assertEqual(failed.devices, ())
        self.assertEqual(failed.error, "hardware database unavailable")

    def test_recommended_driver_reports_an_available_package_update(self):
        output = """== /sys/devices/pci0000:00/0000:01:00.0 ==
vendor   : NVIDIA Corporation
model    : NVIDIA GPU
driver   : nvidia-driver-595-open - distro non-free recommended
"""
        responses = {
            ("apt-cache", "policy", "nvidia-driver-595-open"):
                subprocess.CompletedProcess(
                    [],
                    0,
                    "Installed: 595.58.03-0ubuntu1\n"
                    "Candidate: 595.84-0ubuntu0.26.04.1\n",
                    "",
                ),
            (
                "dpkg", "--compare-versions",
                "595.58.03-0ubuntu1", "lt", "595.84-0ubuntu0.26.04.1",
            ): subprocess.CompletedProcess([], 0, "", ""),
        }
        option = parse_ubuntu_driver_devices(
            output,
            FakeRunner(
                responses,
                installed={"nvidia-driver-595-open"},
                versions={"nvidia-driver-595-open": "595.58.03-0ubuntu1"},
            ),
        )[0].options[0]
        self.assertEqual(option.installed_version, "595.58.03-0ubuntu1")
        self.assertEqual(option.candidate_version, "595.84-0ubuntu0.26.04.1")
        self.assertTrue(option.update_available)

    def test_graphics_marks_only_bound_nvidia_driver_as_active(self):
        path = "/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0"
        output = f"""== {path} ==
modalias : pci:v000010DEd00002584
vendor   : NVIDIA Corporation
model    : GA107 [GeForce RTX 3050 6GB]
driver   : nvidia-driver-595-open - distro non-free recommended
driver   : xserver-xorg-video-nouveau - distro free builtin
"""
        responses = {
            ("lspci", "-k", "-s", "0000:01:00.0"):
                subprocess.CompletedProcess(
                    [],
                    0,
                    "01:00.0 VGA compatible controller: NVIDIA GA107\n"
                    "\tKernel driver in use: nvidia\n"
                    "\tKernel modules: nouveau, nvidia\n",
                    "",
                ),
            ("modinfo", "-F", "version", "nvidia"):
                subprocess.CompletedProcess([], 0, "595.41.02\n", ""),
            (
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ): subprocess.CompletedProcess([], 0, "595.41.02\n", ""),
        }
        devices = parse_ubuntu_driver_devices(
            output,
            FakeRunner(
                responses,
                installed={
                    "nvidia-driver-595-open",
                    "xserver-xorg-video-nouveau",
                },
            ),
        )

        self.assertEqual(devices[0].active_driver, "nvidia")
        self.assertTrue(devices[0].options[0].installed)
        self.assertTrue(devices[0].options[0].active)
        self.assertTrue(devices[0].options[1].installed)
        self.assertFalse(devices[0].options[1].active)
        self.assertTrue(devices[0].active_driver_healthy)
        self.assertEqual(devices[0].active_driver_version, "595.41.02")

    def test_graphics_reports_bound_nvidia_with_broken_userspace_as_unhealthy(self):
        path = "/sys/devices/pci0000:00/0000:01:00.0"
        output = f"""== {path} ==
vendor   : NVIDIA Corporation
model    : NVIDIA GPU
driver   : nvidia-driver-595-open - distro non-free recommended
"""
        responses = {
            ("lspci", "-k", "-s", "0000:01:00.0"):
                subprocess.CompletedProcess(
                    [], 0, "\tKernel driver in use: nvidia\n", ""
                ),
            ("modinfo", "-F", "version", "nvidia"):
                subprocess.CompletedProcess([], 0, "595.41.02\n", ""),
            (
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ): subprocess.CompletedProcess(
                [], 9, "", "Failed to initialize NVML: Driver/library version mismatch\n"
            ),
        }
        device = parse_ubuntu_driver_devices(
            output,
            FakeRunner(responses, installed={"nvidia-driver-595-open"}),
        )[0]
        self.assertEqual(device.active_driver, "nvidia")
        self.assertTrue(device.options[0].active)
        self.assertFalse(device.active_driver_healthy)
        self.assertEqual(device.active_driver_version, "595.41.02")
        self.assertIn("version mismatch", device.active_driver_error)

    def test_graphics_marks_nouveau_active_when_it_owns_the_device(self):
        path = "/sys/devices/pci0000:00/0000:01:00.0"
        output = f"""== {path} ==
modalias : pci:v000010DEd00002584
vendor   : NVIDIA Corporation
model    : GA107 [GeForce RTX 3050 6GB]
driver   : nvidia-driver-595-open - distro non-free recommended
driver   : xserver-xorg-video-nouveau - distro free builtin
"""
        responses = {
            ("lspci", "-k", "-s", "0000:01:00.0"):
                subprocess.CompletedProcess(
                    [], 0, "\tKernel driver in use: nouveau\n", ""
                ),
        }
        devices = parse_ubuntu_driver_devices(
            output,
            FakeRunner(
                responses,
                installed={
                    "nvidia-driver-595-open",
                    "xserver-xorg-video-nouveau",
                },
            ),
        )

        self.assertEqual(devices[0].active_driver, "nouveau")
        self.assertFalse(devices[0].options[0].active)
        self.assertTrue(devices[0].options[1].active)

    def test_graphics_does_not_guess_between_installed_nvidia_variants(self):
        path = "/sys/devices/pci0000:00/0000:01:00.0"
        output = f"""== {path} ==
modalias : pci:v000010DEd00002584
vendor   : NVIDIA Corporation
model    : GA107 [GeForce RTX 3050 6GB]
driver   : nvidia-driver-595-open - distro non-free recommended
driver   : nvidia-driver-595-server-open - distro non-free
"""
        responses = {
            ("lspci", "-k", "-s", "0000:01:00.0"):
                subprocess.CompletedProcess(
                    [], 0, "\tKernel driver in use: nvidia\n", ""
                ),
        }
        devices = parse_ubuntu_driver_devices(
            output,
            FakeRunner(
                responses,
                installed={
                    "nvidia-driver-595-open",
                    "nvidia-driver-595-server-open",
                },
            ),
        )

        self.assertEqual(devices[0].active_driver, "nvidia")
        self.assertTrue(devices[0].driver_state_known)
        self.assertFalse(any(option.active for option in devices[0].options))

    def test_secure_boot_requires_key_certificate_and_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "MOK.priv"
            certificate = Path(directory) / "MOK.der"
            configuration = Path(directory) / "anduinos-sb-sign.conf"
            private.write_text("private")
            certificate.write_text("certificate")
            configuration.write_text("configuration")
            responses = {
                ("mokutil", "--sb-state"): subprocess.CompletedProcess([], 0, "SecureBoot enabled\n", ""),
                ("mokutil", "--test-key", str(certificate)): subprocess.CompletedProcess([], 1, "MOK.der is already enrolled\n", ""),
                ("openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-serial"): subprocess.CompletedProcess([], 0, "serial=AA12BB34\n", ""),
            }
            state = secure_boot_state(
                FakeRunner(responses), private, certificate, configuration
            )
            self.assertTrue(state.ready)
            self.assertEqual(state.certificate_serial, "aa12bb34")

    def test_unsupported_secure_boot_remains_a_known_non_enforcing_state(self):
        state = secure_boot_state(
            FakeRunner(
                {
                    ("mokutil", "--sb-state"): subprocess.CompletedProcess(
                        [], 0, "This system doesn't support Secure Boot\n", ""
                    )
                }
            )
        )
        self.assertEqual(state.status, SecureBootStatus.UNSUPPORTED)
        self.assertTrue(state.ready)
        self.assertTrue(state.enforcement_inactive)

    def test_failed_secure_boot_probe_blocks_driver_readiness(self):
        state = secure_boot_state(FakeRunner())
        self.assertEqual(state.status, SecureBootStatus.UNKNOWN)
        self.assertFalse(state.ready)
        self.assertFalse(state.enforcement_inactive)

    def test_xbox_distinguishes_signature_mismatch_from_missing_module(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(True, True, True, True, "aa12")
        responses = {
            ("modinfo", "-F", "filename", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "/lib/modules/hid-xpadneo.ko\n", ""),
            ("modinfo", "hid-xpadneo"): subprocess.CompletedProcess([], 0, "sig_key: BB:34\n", ""),
            ("lsmod",): subprocess.CompletedProcess([], 0, "hid_xpadneo 40960 0\n", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(responses, installed={"anduinos-xbox-controller-driver"}),
        )
        self.assertTrue(state.installed)
        self.assertTrue(state.module_loaded)
        self.assertTrue(state.module_available)
        self.assertFalse(state.signature_matches)
        self.assertEqual(state.status, XboxStatus.SIGNATURE_MISMATCH)

    def test_xbox_reports_module_missing_for_current_kernel(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(True, True, True, True, "aa12")
        state = xbox_state(
            secure,
            FakeRunner(installed={"anduinos-xbox-controller-driver"}),
        )

        self.assertTrue(state.installed)
        self.assertFalse(state.module_available)
        self.assertEqual(state.status, XboxStatus.MODULE_MISSING)

    def test_xbox_reports_pending_enrollment_separately(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(
            True,
            True,
            True,
            False,
            "aa12",
            enrollment_pending=True,
        )
        responses = {
            ("modinfo", "-F", "filename", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "/lib/modules/hid-xpadneo.ko\n", ""),
            ("modinfo", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "sig_key: AA:12\n", ""),
            ("lsmod",): subprocess.CompletedProcess([], 0, "", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(responses, installed={"anduinos-xbox-controller-driver"}),
        )

        self.assertTrue(state.signature_matches)
        self.assertEqual(state.status, XboxStatus.ENROLLMENT_PENDING)

    def test_xbox_reports_unknown_secure_boot_probe_without_claiming_block(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(
            False,
            True,
            True,
            False,
            None,
            status=SecureBootStatus.UNKNOWN,
        )
        responses = {
            ("modinfo", "-F", "filename", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "/lib/modules/hid-xpadneo.ko\n", ""),
            ("modinfo", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "sig_key: AA:12\n", ""),
            ("lsmod",): subprocess.CompletedProcess([], 0, "", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(responses, installed={"anduinos-xbox-controller-driver"}),
        )

        self.assertEqual(state.status, XboxStatus.SECURE_BOOT_UNKNOWN)

    def test_xbox_reports_failed_load_state_probe_separately(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(False, False, False, False, None)
        responses = {
            ("modinfo", "-F", "filename", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "/lib/modules/hid-xpadneo.ko\n", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(responses, installed={"anduinos-xbox-controller-driver"}),
        )

        self.assertEqual(state.status, XboxStatus.LOAD_STATE_UNKNOWN)

    def test_xbox_treats_available_unloaded_module_as_ready_on_demand(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(True, True, True, True, "aa12")
        responses = {
            ("modinfo", "-F", "filename", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "/lib/modules/hid-xpadneo.ko\n", ""),
            ("modinfo", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "sig_key: AA:12\n", ""),
            ("lsmod",): subprocess.CompletedProcess([], 0, "", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(responses, installed={"anduinos-xbox-controller-driver"}),
        )

        self.assertFalse(state.module_loaded)
        self.assertEqual(state.status, XboxStatus.READY)

    def test_disabled_secure_boot_never_blocks_unsigned_driver_workflows(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(
            False,
            False,
            False,
            False,
            None,
            configuration_present=False,
        )
        responses = {
            ("modinfo", "-F", "filename", "hid-xpadneo"):
                subprocess.CompletedProcess([], 0, "/lib/modules/hid-xpadneo.ko\n", ""),
            ("modinfo", "hid-xpadneo"): subprocess.CompletedProcess(
                [], 0, "sig_key: BB:34\n", ""
            ),
            ("lsmod",): subprocess.CompletedProcess([], 0, "", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(
                responses, installed={"anduinos-xbox-controller-driver"}
            ),
        )
        self.assertTrue(secure.ready)
        self.assertFalse(secure.enrollment_required)
        self.assertEqual(state.status, XboxStatus.READY)

    def test_dkms_health_reports_modules_signed_by_a_different_key(self):
        from anduinos_driver_center.core import SecureBootState

        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "example.ko.zst"
            module.write_text("module")
            secure = SecureBootState(True, True, True, True, "aa12")
            responses = {
                ("modinfo", str(module)): subprocess.CompletedProcess(
                    [], 0, "sig_key: BB:34\n", ""
                ),
            }
            state = dkms_state(secure, FakeRunner(responses), Path(directory))
            self.assertEqual(state.modules, ("example.ko.zst",))
            self.assertEqual(state.untrusted_modules, ("example.ko.zst",))
            self.assertFalse(state.ready)

    def test_audio_reports_packages_files_modules_and_active_drivers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "sof"
            ucm = root / "ucm2"
            firmware.mkdir()
            ucm.mkdir()
            (firmware / "sof-tgl.ri").write_text("firmware")
            (ucm / "HiFi.conf").write_text("profile")
            responses = {
                ("lsmod",): subprocess.CompletedProcess(
                    [], 0, "snd_sof 438272 1\nsnd_hda_intel 65536 2\n", ""
                ),
                ("lspci", "-nnk"): subprocess.CompletedProcess(
                    [],
                    0,
                    "00:1f.3 Audio device [0403]: Intel Audio\n"
                    "\tKernel driver in use: snd_hda_intel\n",
                    "",
                ),
            }
            installed = {"firmware-sof-anduinos", "alsa-ucm-conf-anduinos"}
            versions = {
                "firmware-sof-anduinos": "2.0.1-1+resolute",
                "alsa-ucm-conf-anduinos": "2.0.0-1+resolute",
            }
            state = audio_state(
                FakeRunner(responses, installed, versions),
                (firmware,),
                ucm,
            )
            self.assertTrue(state.ready)
            self.assertEqual(state.sof_modules, ("snd_sof",))
            self.assertEqual(state.active_drivers, ("snd_hda_intel",))
            self.assertEqual(
                state.sof_package.version, "2.0.1-1+resolute"
            )

    def test_audio_missing_packages_are_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            state = audio_state(
                FakeRunner(),
                (missing,),
                missing,
            )
            self.assertFalse(state.packages_installed)
            self.assertFalse(state.ready)
            self.assertIsNone(state.sof_package.version)

    def test_printing_reports_services_queues_default_and_packages(self):
        responses = {
            ("systemctl", "is-active", "cups.service"):
                subprocess.CompletedProcess([], 0, "active\n", ""),
            ("systemctl", "is-active", "cups.socket"):
                subprocess.CompletedProcess([], 3, "inactive\n", ""),
            ("systemctl", "is-enabled", "cups.service"):
                subprocess.CompletedProcess([], 0, "enabled\n", ""),
            ("systemctl", "is-enabled", "cups.socket"):
                subprocess.CompletedProcess([], 0, "enabled\n", ""),
            ("lpstat", "-p"): subprocess.CompletedProcess(
                [],
                0,
                "printer Office is idle. enabled since Monday\n"
                "printer Lab disabled since Tuesday\n",
                "",
            ),
            ("lpstat", "-d"): subprocess.CompletedProcess(
                [], 0, "system default destination: Office\n", ""
            ),
        }
        installed = {
            "cups",
            "cups-client",
            "cups-core-drivers",
            "cups-filters",
            "cups-filters-core-drivers",
            "cups-ipp-utils",
        }
        state = printing_state(
            FakeRunner(
                responses,
                installed,
                {"cups": "2.4.16-1ubuntu1.3"},
            )
        )
        self.assertTrue(state.service_running)
        self.assertTrue(state.startup_enabled)
        self.assertEqual(state.printers, ("Office", "Lab"))
        self.assertEqual(state.disabled_printers, ("Lab",))
        self.assertEqual(state.default_printer, "Office")
        self.assertEqual(state.core_packages[0].version, "2.4.16-1ubuntu1.3")
        self.assertTrue(state.core_ready)
        self.assertFalse(state.queues_ready)
        self.assertEqual(state.missing_required_packages, ())

    def test_printing_handles_missing_service_and_no_queues(self):
        state = printing_state(FakeRunner())
        self.assertFalse(state.service_running)
        self.assertFalse(state.startup_enabled)
        self.assertEqual(state.printers, ())
        self.assertIsNone(state.default_printer)
        self.assertFalse(state.core_ready)
        self.assertEqual(len(state.missing_packages), 12)
        self.assertEqual(len(state.missing_required_packages), 6)


if __name__ == "__main__":
    unittest.main()
