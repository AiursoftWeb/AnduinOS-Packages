import tempfile
import unittest
from pathlib import Path

from installer_core.hostnames import (
    HostnameError,
    HostnameErrorCode,
    detect_device_type,
    generate_random_suffix,
    is_canonical_hostname,
    is_valid_hostname_input,
    normalize_hostname,
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


class HostnameNormalizationTests(unittest.TestCase):
    def test_accepts_rfc_case_and_returns_systemd_canonical_hostname(self):
        self.assertEqual(normalize_hostname("TT-VIEW-71"), "tt-view-71")
        self.assertEqual(normalize_hostname("71-view-TT"), "71-view-tt")
        self.assertEqual(normalize_hostname("a"), "a")

    def test_rejects_non_ascii_and_non_ldh_characters_before_normalizing(self):
        for value in ("tt_view_71", "tt.view.71", "ＴＴ-view", "Kelvin"):
            with self.subTest(value=value), self.assertRaisesRegex(
                HostnameError, "ASCII letters, numbers, and hyphens"
            ):
                normalize_hostname(value)

    def test_rejects_empty_overlong_and_edge_hyphen_with_stable_codes(self):
        cases = (
            ("", HostnameErrorCode.REQUIRED),
            ("a" * 64, HostnameErrorCode.TOO_LONG),
            ("-host", HostnameErrorCode.EDGE_HYPHEN),
            ("host-", HostnameErrorCode.EDGE_HYPHEN),
        )
        for value, code in cases:
            with self.subTest(value=value), self.assertRaises(
                HostnameError
            ) as raised:
                normalize_hostname(value)
            self.assertIs(raised.exception.code, code)

    def test_input_and_plan_predicates_have_distinct_contracts(self):
        self.assertTrue(is_valid_hostname_input("TT-VIEW-71"))
        self.assertTrue(is_canonical_hostname("tt-view-71"))
        self.assertFalse(is_canonical_hostname("TT-VIEW-71"))
        self.assertFalse(is_valid_hostname_input("tt_view_71"))


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
