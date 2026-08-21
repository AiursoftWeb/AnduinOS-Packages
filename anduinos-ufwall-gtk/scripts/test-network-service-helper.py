from importlib.machinery import SourceFileLoader
from pathlib import Path
import types
import unittest
from unittest.mock import call, patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader(
    "network_service_helper", str(ROOT / "scripts/network-service-helper")
)
helper = types.ModuleType(loader.name)
loader.exec_module(helper)


class NetworkServiceHelperTests(unittest.TestCase):
    def test_disable_masks_daemon_and_socket(self):
        with patch.object(helper, "run") as run:
            helper.set_mdns_enabled(False)
        run.assert_called_once_with(
            ["systemctl", "mask", "--now", *helper.MDNS_UNITS]
        )

    def test_enable_unmasks_before_starting_units(self):
        with patch.object(helper, "run") as run:
            helper.set_mdns_enabled(True)
        self.assertEqual(
            run.call_args_list,
            [
                call(["systemctl", "unmask", *helper.MDNS_UNITS]),
                call(["systemctl", "enable", "--now", *helper.MDNS_UNITS]),
            ],
        )

    def test_unit_allowlist_is_fixed(self):
        self.assertEqual(
            helper.MDNS_UNITS,
            ("avahi-daemon.service", "avahi-daemon.socket"),
        )

    def test_polkit_action_only_authorizes_the_fixed_helper(self):
        tree = ET.parse(ROOT / "data/com.anduinos.ufwall.policy")
        action = tree.find(
            ".//action[@id='com.anduinos.ufwall.manage-network-discovery']"
        )
        self.assertIsNotNone(action)
        annotations = {
            node.attrib.get("key"): (node.text or "").strip()
            for node in action.findall(".//annotate")
        }
        self.assertEqual(
            annotations["org.freedesktop.policykit.exec.path"],
            "/usr/libexec/ufwall-gtk/network-service-helper",
        )
        self.assertNotIn(
            "org.freedesktop.policykit.exec.allow_gui", annotations
        )


if __name__ == "__main__":
    unittest.main()
