from importlib.machinery import SourceFileLoader
from pathlib import Path
import types
import unittest
from unittest.mock import patch


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
            patch.object(driver_helper, "configure_dkms_signing") as signing,
            patch.object(driver_helper, "apt_update") as update,
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_driver("nvidia-driver-595-open")
        signing.assert_called_once_with()
        update.assert_called_once_with()
        run.assert_called_once_with(["ubuntu-drivers", "install", "nvidia-driver-595-open"])

    def test_xbox_package_name_is_fixed(self):
        with (
            patch.object(driver_helper, "configure_dkms_signing"),
            patch.object(driver_helper, "apt_update"),
            patch.object(driver_helper, "run") as run,
        ):
            driver_helper.install_xbox(reinstall=True)
        run.assert_called_once_with(
            ["apt-get", "install", "-y", "--reinstall", "anduinos-xbox-controller-driver"]
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


if __name__ == "__main__":
    unittest.main()
