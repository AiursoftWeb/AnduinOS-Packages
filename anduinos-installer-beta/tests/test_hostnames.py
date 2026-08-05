import tempfile
import unittest
from pathlib import Path

from installer_core.hostnames import (
    detect_device_type,
    generate_random_suffix,
    suggest_hostname,
)


class HostnameSuggestionTests(unittest.TestCase):
    def test_suggests_username_device_type_and_random_suffix(self):
        self.assertEqual(
            suggest_hostname("anduin", "laptop", "a3f9"),
            "anduin-laptop-a3f9",
        )
        self.assertEqual(
            suggest_hostname("alice", "desktop", "71c2"),
            "alice-desktop-71c2",
        )

    def test_empty_username_uses_anduinos_until_account_name_is_known(self):
        self.assertEqual(
            suggest_hostname("", "desktop", "000f"),
            "anduinos-desktop-000f",
        )

    def test_suggestion_is_a_valid_dns_label_with_bounded_length(self):
        suggestion = suggest_hostname(
            "A Very Long User Name!" * 8, "laptop", "beef"
        )
        self.assertLessEqual(len(suggestion), 63)
        self.assertRegex(suggestion, r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
        self.assertTrue(suggestion.endswith("-laptop-beef"))

    def test_rejects_malformed_random_suffix(self):
        for suffix in ("abc", "ABCDE", "zzzz", "a3-f"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                suggest_hostname("alice", "desktop", suffix)

    def test_random_suffix_is_zero_padded_lowercase_hex(self):
        self.assertEqual(generate_random_suffix(lambda _limit: 0), "0000")
        self.assertEqual(generate_random_suffix(lambda _limit: 0xA3F9), "a3f9")


class DeviceTypeDetectionTests(unittest.TestCase):
    def test_dmi_laptop_chassis_is_laptop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chassis = root / "chassis_type"
            chassis.write_text("10\n", encoding="utf-8")
            self.assertEqual(
                detect_device_type(chassis, root / "missing-power"),
                "laptop",
            )

    def test_dmi_desktop_chassis_wins_over_peripheral_battery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chassis = root / "chassis_type"
            chassis.write_text("3\n", encoding="utf-8")
            mouse = root / "power" / "hidpp_battery_0"
            mouse.mkdir(parents=True)
            (mouse / "type").write_text("Battery\n", encoding="utf-8")
            self.assertEqual(
                detect_device_type(chassis, root / "power"), "desktop"
            )

    def test_system_battery_is_fallback_for_devices_without_dmi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery = root / "power" / "battery"
            battery.mkdir(parents=True)
            (battery / "type").write_text("Battery\n", encoding="utf-8")
            (battery / "scope").write_text("System\n", encoding="utf-8")
            self.assertEqual(
                detect_device_type(root / "missing-chassis", root / "power"),
                "laptop",
            )

    def test_unknown_hardware_falls_back_to_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                detect_device_type(root / "chassis", root / "power"),
                "desktop",
            )


if __name__ == "__main__":
    unittest.main()
