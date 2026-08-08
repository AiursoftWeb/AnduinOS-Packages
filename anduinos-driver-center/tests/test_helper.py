from importlib.machinery import SourceFileLoader
from pathlib import Path
import types
import unittest
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader("driver_helper", str(ROOT / "scripts/driver-helper"))
driver_helper = types.ModuleType(loader.name)
loader.exec_module(driver_helper)


class HelperTests(unittest.TestCase):
    def test_rejects_package_not_reported_by_ubuntu_drivers(self):
        with patch.object(driver_helper, "available_driver_packages", return_value={"nvidia-driver-595-open"}):
            with self.assertRaises(ValueError):
                driver_helper.install_driver("definitely-not-a-driver")

    def test_selected_graphics_driver_is_delegated_to_ubuntu_drivers(self):
        with (
            patch.object(driver_helper, "available_driver_packages", return_value={"nvidia-driver-595-open"}),
            patch.object(driver_helper, "package_is_installed", return_value=False),
            patch.object(driver_helper, "apt_update") as update,
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_driver("nvidia-driver-595-open")
        update.assert_called_once_with()
        run.assert_called_once_with(["ubuntu-drivers", "install", "nvidia-driver-595-open"])

    def test_existing_graphics_driver_is_repaired_after_ubuntu_drivers(self):
        with (
            patch.object(driver_helper, "available_driver_packages", return_value={"nvidia-driver-595-open"}),
            patch.object(driver_helper, "package_is_installed", return_value=True),
            patch.object(driver_helper, "apt_update"),
            patch.object(driver_helper, "repair_nvidia_stack") as repair,
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_driver("nvidia-driver-595-open")
        run.assert_called_once_with(
            ["ubuntu-drivers", "install", "nvidia-driver-595-open"]
        )
        repair.assert_called_once_with("nvidia-driver-595-open")

    def test_nvidia_repair_selects_only_requested_family_and_running_kernel(self):
        installed = [
            "libnvidia-compute-595:amd64",
            "nvidia-dkms-595-open",
            "nvidia-driver-595-open",
            "nvidia-firmware-595-595.10.20",
            "nvidia-utils-595",
            "xserver-xorg-video-nvidia-595",
            "linux-modules-nvidia-595-open-test-kernel",
            "linux-modules-nvidia-595-open-old-kernel",
            "nvidia-driver-580-open",
            "nvidia-utils-580",
            "nvidia-dkms-595-server-open",
            "libnvidia-egl-wayland1:amd64",
        ]
        with patch.object(
            driver_helper, "installed_nvidia_packages", return_value=installed
        ):
            selected = driver_helper.nvidia_repair_packages(
                "nvidia-driver-595-open", "test-kernel"
            )
        self.assertEqual(
            selected,
            sorted(
                [
                    "libnvidia-compute-595:amd64",
                    "linux-modules-nvidia-595-open-test-kernel",
                    "nvidia-dkms-595-open",
                    "nvidia-driver-595-open",
                    "nvidia-firmware-595-595.10.20",
                    "nvidia-utils-595",
                    "xserver-xorg-video-nvidia-595",
                ]
            ),
        )

    def test_nvidia_repair_never_loads_the_live_graphics_module(self):
        with (
            patch.object(
                driver_helper,
                "installed_nvidia_packages",
                return_value=["nvidia-driver-595-open", "nvidia-utils-595"],
            ),
            patch.object(
                driver_helper.os,
                "uname",
                return_value=types.SimpleNamespace(release="test-kernel"),
            ),
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.repair_nvidia_stack("nvidia-driver-595-open")
        self.assertEqual(
            run.call_args_list,
            [
                call([
                    "apt-get", "install", "-y", "--reinstall",
                    "nvidia-driver-595-open", "nvidia-utils-595",
                ]),
                call(["depmod", "-a", "test-kernel"]),
                call(["modinfo", "-k", "test-kernel", "nvidia"]),
            ],
        )

    def test_repair_action_validates_and_preserves_selected_package(self):
        with (
            patch.object(
                driver_helper,
                "available_driver_packages",
                return_value={"nvidia-driver-595-open"},
            ),
            patch.object(driver_helper, "package_is_installed", return_value=True),
            patch.object(driver_helper, "apt_update") as update,
            patch.object(driver_helper, "repair_nvidia_stack") as repair,
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.repair_nvidia_driver("nvidia-driver-595-open")
        update.assert_called_once_with()
        run.assert_called_once_with(
            ["ubuntu-drivers", "install", "nvidia-driver-595-open"]
        )
        repair.assert_called_once_with("nvidia-driver-595-open")

    def test_xbox_package_name_is_fixed(self):
        with (
            patch.object(driver_helper, "apt_update"),
            patch.object(driver_helper, "run") as run,
            patch.object(
                driver_helper.os,
                "uname",
                return_value=types.SimpleNamespace(release="test-kernel"),
            ),
        ):
            driver_helper.install_xbox(reinstall=True)
        self.assertEqual(
            run.call_args_list,
            [
                call([
                    "apt-get", "install", "-y", "--reinstall",
                    "linux-headers-test-kernel",
                    "anduinos-xbox-controller-driver",
                ]),
                call(["dkms", "autoinstall", "-k", "test-kernel"]),
                call(["depmod", "-a", "test-kernel"]),
                call(["modinfo", "-k", "test-kernel", "hid-xpadneo"]),
            ],
        )

    def test_audio_package_names_are_fixed(self):
        with (
            patch.object(driver_helper, "apt_update") as update,
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_audio()
        update.assert_called_once_with()
        run.assert_called_once_with(
            [
                "apt-get",
                "install",
                "-y",
                "firmware-sof-anduinos",
                "alsa-ucm-conf-anduinos",
            ]
        )

    def test_audio_repair_reinstalls_both_support_packages(self):
        with (
            patch.object(driver_helper, "apt_update"),
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_audio(reinstall=True)
        run.assert_called_once_with(
            ["apt-get", "install", "-y", "--reinstall", *driver_helper.AUDIO_PACKAGES]
        )

    def test_printing_support_package_names_are_fixed(self):
        with (
            patch.object(driver_helper, "apt_update") as update,
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_printing_support()
        update.assert_called_once_with()
        run.assert_called_once_with(
            ["apt-get", "install", "-y", *driver_helper.PRINTING_PACKAGES]
        )
        self.assertEqual(
            driver_helper.PRINTING_PACKAGES,
            (
                "cups",
                "cups-client",
                "cups-core-drivers",
                "cups-filters",
                "cups-filters-core-drivers",
                "cups-ipp-utils",
                "cups-browsed",
                "avahi-daemon",
                "ipp-usb",
                "cups-pk-helper",
                "printer-driver-all",
                "sane-airscan",
            ),
        )

    def test_disabling_printing_masks_every_activation_path(self):
        with patch.object(driver_helper, "run") as run:
            driver_helper.set_printing_enabled(False)
        run.assert_called_once_with(
            ["systemctl", "mask", "--now", *driver_helper.PRINTING_UNITS]
        )

    def test_enabling_printing_unmasks_then_starts_autostart_units(self):
        with patch.object(driver_helper, "run") as run:
            driver_helper.set_printing_enabled(True)
        self.assertEqual(
            run.call_args_list,
            [
                call(
                    ["systemctl", "unmask", *driver_helper.PRINTING_UNITS]
                ),
                call(
                    [
                        "systemctl",
                        "enable",
                        "--now",
                        *driver_helper.PRINTING_AUTOSTART_UNITS,
                    ]
                ),
            ],
        )

    def test_resume_print_queues_only_enables_paused_queues(self):
        result = types.SimpleNamespace(
            returncode=0,
            stdout=(
                "printer Office is idle. enabled since Monday\n"
                "printer Lab disabled since Tuesday\n"
            ),
            stderr="",
        )
        with (
            patch.object(driver_helper.subprocess, "run", return_value=result),
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.resume_print_queues()
        run.assert_called_once_with(["cupsenable", "Lab"])


if __name__ == "__main__":
    unittest.main()
