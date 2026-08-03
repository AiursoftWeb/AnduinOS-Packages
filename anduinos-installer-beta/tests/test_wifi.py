import subprocess
import unittest

from installer_core.wifi import (
    parse_wifi_networks,
    scan_wifi_networks,
    set_wifi_radio,
    split_nmcli_terse,
    wifi_radio_enabled,
)


class WifiDiscoveryTests(unittest.TestCase):
    def test_split_preserves_escaped_colons_and_backslashes(self):
        self.assertEqual(
            split_nmcli_terse(r"*:Cafe\: Guest:91:WPA2\\WPA3"),
            ("*", "Cafe: Guest", "91", "WPA2\\WPA3"),
        )

    def test_scan_results_are_deduplicated_and_ranked(self):
        networks = parse_wifi_networks(
            "*:Home:62:WPA2\n"
            ":Cafe\\: Guest:88:--\n"
            ":Home:95:WPA2\n"
            ":Hidden:invalid:WPA2\n"
            ":This record is incomplete\n"
        )
        self.assertEqual(
            [(item.ssid, item.signal, item.security, item.active)
             for item in networks],
            [
                ("Home", 62, "WPA2", True),
                ("Cafe: Guest", 88, "--", False),
                ("Hidden", 0, "WPA2", False),
            ],
        )

    def test_scan_is_read_only_and_reports_network_manager_errors(self):
        commands = []

        def run(command, **kwargs):
            commands.append((command, kwargs))
            return subprocess.CompletedProcess(command, 10, "", "Wi-Fi disabled")

        with self.assertRaisesRegex(RuntimeError, "Wi-Fi disabled"):
            scan_wifi_networks(run)
        self.assertEqual(
            commands[0][0][-4:], ("wifi", "list", "--rescan", "yes")
        )
        self.assertNotIn("connect", commands[0][0])

    def test_radio_state_accepts_only_network_manager_states(self):
        def enabled(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, "enabled\n", "")

        def disabled(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, "disabled\n", "")

        self.assertTrue(wifi_radio_enabled(enabled))
        self.assertFalse(wifi_radio_enabled(disabled))

    def test_radio_toggle_uses_only_the_requested_nmcli_command(self):
        commands = []

        def run(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        set_wifi_radio(True, run)
        set_wifi_radio(False, run)
        self.assertEqual(
            commands,
            [
                ("nmcli", "radio", "wifi", "on"),
                ("nmcli", "radio", "wifi", "off"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
