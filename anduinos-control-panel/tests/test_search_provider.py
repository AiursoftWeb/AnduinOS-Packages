from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anduinos_control_panel.search_provider import (  # noqa: E402
    CONTROL_PANEL_EXECUTABLE,
    SEARCH_PROVIDER_XML,
    _activation_arguments,
    _result_ids,
    _result_metas,
)


class SearchProviderTests(unittest.TestCase):
    def test_exports_the_complete_search_provider_two_contract(self):
        node = Gio.DBusNodeInfo.new_for_xml(SEARCH_PROVIDER_XML)
        interface = node.interfaces[0]
        self.assertEqual(interface.name, "org.gnome.Shell.SearchProvider2")
        self.assertEqual(
            {method.name for method in interface.methods},
            {
                "GetInitialResultSet",
                "GetSubsearchResultSet",
                "GetResultMetas",
                "ActivateResult",
                "LaunchSearch",
            },
        )

    def test_search_results_are_topic_ids_with_shell_metadata(self):
        identifiers = _result_ids(["swap"])
        self.assertEqual(identifiers, ["system.virtual-memory"])
        metas = _result_metas(identifiers)
        serialized = GLib.Variant("(aa{sv})", (metas,)).unpack()[0]
        self.assertEqual(serialized[0]["id"], "system.virtual-memory")
        self.assertEqual(serialized[0]["name"], "Virtual Memory Settings")
        self.assertIn("swap", serialized[0]["description"].lower())
        self.assertEqual(serialized[0]["gicon"], "com.anduinos.swapcontrol")

    def test_subsearch_can_only_refine_previous_results(self):
        previous = _result_ids(["memory"])
        refined = _result_ids(["virtual", "memory"], previous)
        self.assertEqual(refined, ["system.virtual-memory"])

    def test_external_results_open_directly_and_internal_results_deep_link(self):
        self.assertEqual(_activation_arguments("network.firewall"), ["ufwall-gtk"])
        self.assertEqual(
            _activation_arguments("system.startup-boot"),
            [CONTROL_PANEL_EXECUTABLE, "--topic", "system.startup-boot"],
        )
        self.assertIsNone(_activation_arguments("unknown.topic"))

    def test_missing_recommended_tool_routes_to_the_install_prompt(self):
        with patch(
            "anduinos_control_panel.search_provider.shutil.which",
            return_value=None,
        ):
            self.assertEqual(
                _activation_arguments("network.advanced"),
                [CONTROL_PANEL_EXECUTABLE, "--topic", "network.advanced"],
            )
            self.assertEqual(
                _activation_arguments("hardware.scanners"),
                [CONTROL_PANEL_EXECUTABLE, "--topic", "hardware.scanners"],
            )

        with patch(
            "anduinos_control_panel.search_provider.shutil.which",
            return_value="/usr/bin/tool",
        ):
            self.assertEqual(
                _activation_arguments("network.advanced"),
                ["nm-connection-editor"],
            )
            self.assertEqual(
                _activation_arguments("hardware.scanners"), ["simple-scan"]
            )


if __name__ == "__main__":
    unittest.main()
