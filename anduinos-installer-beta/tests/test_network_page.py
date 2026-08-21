import unittest
from pathlib import Path

from gi.repository import Gio

from pages import (
    _input_method_install_label,
    effective_network_choice,
    effective_network_input_methods,
    internet_connection_ready,
    normalize_input_method_choices,
    should_show_network_page,
)
from languages import INPUT_METHODS, input_method


class FakeNetworkMonitor:
    def __init__(self, connectivity=None, error=None):
        self.connectivity = connectivity
        self.error = error

    def get_connectivity(self):
        if self.error is not None:
            raise self.error
        return self.connectivity


class NetworkPageRoutingTests(unittest.TestCase):
    def test_wifi_interactions_stay_inside_the_installer(self):
        source = (Path(__file__).parents[1] / "src/pages.py").read_text()
        network_page = source.split("def build_network_page", 1)[1].split(
            "def build_keyboard_page", 1
        )[0]
        self.assertNotIn("gnome-control-center", network_page)
        self.assertIn("connect_wifi(", network_page)
        self.assertIn("disconnect_wifi(", network_page)
        self.assertIn("Connect to a hidden network", network_page)
        self.assertIn("Use WPS", network_page)
        self.assertIn('page.connect("map", _page_mapped)', network_page)
        self.assertIn('page.connect("unmap", _page_unmapped)', network_page)
        self.assertNotIn('page.connect("notify::mapped"', network_page)
        self.assertIn("radio_spinner = Gtk.Spinner(", network_page)
        self.assertIn("visible=False,", network_page)
        self.assertEqual(network_page.count('add_css_class("circular")'), 2)
        self.assertGreaterEqual(network_page.count("width_request=40"), 2)
        self.assertIn("WPA/WPA2 PSK", network_page)
        self.assertIn("WPA3 SAE", network_page)
        self.assertIn("WPA/WPA2 802.1X", network_page)

    def test_input_method_label_explains_language_before_product(self):
        rime = input_method("rime")
        self.assertIsNotNone(rime)
        self.assertEqual(
            _input_method_install_label(rime, "zh_CN"),
            "安装简体中文输入法：AnduinOS Rime",
        )

    def test_every_input_method_label_comes_from_policy_metadata(self):
        for method in INPUT_METHODS.values():
            with self.subTest(method=method.id):
                label = _input_method_install_label(method, "en_US")
                self.assertIn(method.language_name, label)
                self.assertIn(method.display_name, label)

    def test_offline_choices_are_false_without_forgetting_preference(self):
        for preferred, online, expected in (
            (True, True, True),
            (False, True, False),
            (True, False, False),
            (False, False, False),
        ):
            with self.subTest(preferred=preferred, online=online):
                self.assertEqual(
                    effective_network_choice(preferred, online), expected
                )

    def test_offline_input_methods_are_all_unselected(self):
        self.assertEqual(
            effective_network_input_methods(("rime", "wubi"), True),
            ("rime", "wubi"),
        )
        self.assertEqual(
            effective_network_input_methods(("rime", "wubi"), False),
            (),
        )
        self.assertEqual(effective_network_input_methods((), True), ())

    def test_input_method_selection_is_normalized_to_policy_order(self):
        self.assertEqual(
            normalize_input_method_choices(
                ("rime", "wubi"), ("wubi", "unknown", "rime", "wubi")
            ),
            ("rime", "wubi"),
        )
        self.assertEqual(
            normalize_input_method_choices(("rime", "wubi"), None), ()
        )

    def test_only_full_connectivity_skips_the_network_page(self):
        for connectivity in Gio.NetworkConnectivity:
            with self.subTest(connectivity=connectivity):
                self.assertEqual(
                    internet_connection_ready(
                        FakeNetworkMonitor(connectivity=connectivity)
                    ),
                    connectivity == Gio.NetworkConnectivity.FULL,
                )

    def test_detection_errors_keep_the_network_page_available(self):
        self.assertFalse(
            internet_connection_ready(
                FakeNetworkMonitor(error=RuntimeError("monitor unavailable"))
            )
        )

    def test_development_mode_always_shows_the_network_page(self):
        full = FakeNetworkMonitor(connectivity=Gio.NetworkConnectivity.FULL)
        self.assertTrue(
            should_show_network_page({"development_mode": True}, full)
        )
        self.assertFalse(
            should_show_network_page({"development_mode": False}, full)
        )


if __name__ == "__main__":
    unittest.main()
