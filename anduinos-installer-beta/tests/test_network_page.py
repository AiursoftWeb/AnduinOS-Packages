import unittest

from gi.repository import Gio

from pages import internet_connection_ready, should_show_network_page


class FakeNetworkMonitor:
    def __init__(self, connectivity=None, error=None):
        self.connectivity = connectivity
        self.error = error

    def get_connectivity(self):
        if self.error is not None:
            raise self.error
        return self.connectivity


class NetworkPageRoutingTests(unittest.TestCase):
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
