import pathlib
import subprocess
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import importlib.machinery


SCRIPT = pathlib.Path(__file__).parents[1] / "assets" / "anduinos-oobe"
oobe = importlib.machinery.SourceFileLoader(
    "anduinos_oobe_privacy", str(SCRIPT)
).load_module()
HELPER_SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "network-service-helper"
helper = importlib.machinery.SourceFileLoader(
    "anduinos_oobe_network_helper", str(HELPER_SCRIPT)
).load_module()
POLICY = pathlib.Path(__file__).parents[1] / "data" / "com.anduinos.oobe.policy"


class PrivacyTests(unittest.TestCase):
    def completed(self, stdout="", returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def test_masked_avahi_is_available_but_disabled(self):
        with (
            mock.patch.object(oobe.os.path, "isfile", return_value=True),
            mock.patch.object(
                oobe.subprocess,
                "run",
                side_effect=[
                    self.completed("masked\n"),
                    self.completed("masked\n", 1),
                    self.completed("masked\n", 1),
                ],
            ),
        ):
            self.assertEqual(oobe.get_mdns_control_state(), (True, False))

    def test_missing_avahi_is_unavailable(self):
        with (
            mock.patch.object(oobe.os.path, "isfile", return_value=True),
            mock.patch.object(
                oobe.subprocess, "run",
                return_value=self.completed("not-found\n"),
            ),
        ):
            self.assertEqual(oobe.get_mdns_control_state(), (False, False))

    def test_toggle_uses_only_the_fixed_privileged_helper(self):
        with mock.patch.object(
            oobe.subprocess, "run", return_value=self.completed()
        ) as run:
            oobe.set_mdns_enabled(False)

        run.assert_called_once_with(
            [
                "pkexec",
                "/usr/libexec/anduinos-oobe/network-service-helper",
                "set-mdns-enabled",
                "false",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_helper_controls_only_avahi_units(self):
        self.assertEqual(
            helper.MDNS_UNITS,
            ("avahi-daemon.service", "avahi-daemon.socket"),
        )
        with mock.patch.object(helper, "run") as run:
            helper.set_mdns_enabled(False)
        run.assert_called_once_with(
            ["systemctl", "mask", "--now", *helper.MDNS_UNITS]
        )

    def test_policy_authorizes_only_oobe_fixed_helper(self):
        root = ET.parse(POLICY).getroot()
        action = root.find(
            "./action[@id='com.anduinos.oobe.manage-network-discovery']"
        )
        self.assertIsNotNone(action)
        annotations = {
            node.attrib["key"]: node.text for node in action.findall("annotate")
        }
        self.assertEqual(
            annotations.get("org.freedesktop.policykit.exec.path"),
            "/usr/libexec/anduinos-oobe/network-service-helper",
        )
        self.assertNotIn("org.freedesktop.policykit.exec.allow_gui", annotations)


if __name__ == "__main__":
    unittest.main()
