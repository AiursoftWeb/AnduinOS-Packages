import importlib.machinery
import pathlib
import subprocess
import types
import unittest
from unittest import mock

from gi.repository import Gio


SCRIPT = pathlib.Path(__file__).parents[1] / "assets" / "anduinos-oobe"
oobe = importlib.machinery.SourceFileLoader("anduinos_oobe", str(SCRIPT)).load_module()


class FakeNetworkMonitor:
    def __init__(self, connectivity=None, error=None):
        self.connectivity = connectivity
        self.error = error

    def get_connectivity(self):
        if self.error is not None:
            raise self.error
        return self.connectivity


class NetworkTests(unittest.TestCase):
    def test_only_full_connectivity_is_ready(self):
        for connectivity in Gio.NetworkConnectivity:
            with self.subTest(connectivity=connectivity):
                self.assertEqual(
                    oobe.internet_connection_ready(
                        FakeNetworkMonitor(connectivity=connectivity)
                    ),
                    connectivity == Gio.NetworkConnectivity.FULL,
                )

    def test_detection_error_is_treated_as_offline(self):
        self.assertFalse(
            oobe.internet_connection_ready(
                FakeNetworkMonitor(error=RuntimeError("monitor unavailable"))
            )
        )

    def test_wifi_scan_deduplicates_and_prioritizes_active_network(self):
        output = (
            "*:Home:62:WPA2\n"
            ":Cafe\\: Guest:88:--\n"
            ":Home:95:WPA2\n"
            ":Hidden:invalid:WPA2\n"
        )
        completed = subprocess.CompletedProcess(
            args=["nmcli"], returncode=0, stdout=output, stderr=""
        )
        with mock.patch.object(oobe.subprocess, "run", return_value=completed):
            networks = oobe._scan_wifi_networks()

        self.assertEqual(
            [
                (item["ssid"], item["signal"], item["security"], item["active"])
                for item in networks
            ],
            [
                ("Home", 62, "WPA2", True),
                ("Cafe: Guest", 88, "--", False),
                ("Hidden", 0, "WPA2", False),
            ],
        )

    def test_network_devices_cover_no_wired_wireless_and_mixed_hardware(self):
        cases = (
            ("lo:loopback:connected (externally)\n", (0, 0)),
            ("enp1s0:ethernet:disconnected\n", (0, 1)),
            ("wlp2s0:wifi:disconnected\n", (1, 0)),
            (
                "enp1s0:ethernet:connected\n"
                "wlp2s0:wifi:disconnected\n",
                (1, 1),
            ),
        )
        for output, expected in cases:
            with self.subTest(output=output):
                devices = oobe._parse_network_devices(output)
                self.assertEqual(
                    (len(devices["wifi"]), len(devices["ethernet"])),
                    expected,
                )

    def test_offline_startup_inserts_network_page_after_welcome(self):
        window = types.SimpleNamespace(
            is_oobe=True,
            _update_nav_buttons=lambda: None,
            _finish_oobe=lambda: None,
        )

        def factories(online):
            with (
                mock.patch.object(
                    oobe, "internet_connection_ready", return_value=online
                ),
                mock.patch.object(oobe, "has_nvidia_gpu", return_value=False),
                mock.patch.object(oobe, "is_virtual_machine", return_value=True),
                mock.patch.object(oobe, "is_arm64", return_value=True),
                mock.patch.object(oobe, "is_chinese_locale", return_value=False),
                mock.patch.object(
                    oobe.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 1, "", ""),
                ),
            ):
                return oobe.OobeWindow._get_page_factories(
                    window, navigate_next=lambda: None
                )

        offline = factories(False)
        online = factories(True)
        self.assertEqual(len(offline), len(online) + 1)
        self.assertIn("create_welcome_page", offline[0].__code__.co_names)
        self.assertIn("create_network_page", offline[1].__code__.co_names)
        self.assertIn("create_appearance_page", offline[2].__code__.co_names)
        self.assertIn("create_appearance_page", online[1].__code__.co_names)

    def test_chinese_flathub_mirror_precedes_bottles_offer(self):
        window = types.SimpleNamespace(
            is_oobe=True,
            _update_nav_buttons=lambda: None,
            _finish_oobe=lambda: None,
        )

        with (
            mock.patch.object(
                oobe, "internet_connection_ready", return_value=True
            ),
            mock.patch.object(oobe, "has_nvidia_gpu", return_value=False),
            mock.patch.object(oobe, "is_virtual_machine", return_value=True),
            mock.patch.object(oobe, "is_arm64", return_value=False),
            mock.patch.object(oobe, "is_chinese_locale", return_value=True),
            mock.patch.object(
                oobe.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, "", ""),
            ),
        ):
            factories = oobe.OobeWindow._get_page_factories(
                window, navigate_next=lambda: None
            )

        factory_names = [factory.__code__.co_names for factory in factories]
        mirror_index = next(
            index
            for index, names in enumerate(factory_names)
            if "create_flathub_mirror_page" in names
        )
        bottles_index = next(
            index
            for index, names in enumerate(factory_names)
            if "create_exe_sandbox_page" in names
        )
        self.assertLess(mirror_index, bottles_index)

    def test_navigation_refresh_waits_until_controls_are_ready(self):
        building_window = types.SimpleNamespace(_nav_ready=False)

        # The offline network page refreshes navigation while it is being
        # constructed.  This must not touch controls that do not exist yet.
        oobe.OobeWindow._update_nav_buttons(building_window)

        page = types.SimpleNamespace(
            _hide_next=True,
            _block_carousel=True,
            _suggest_next=False,
        )
        carousel = mock.Mock()
        carousel.get_position.return_value = 1
        carousel.get_n_pages.return_value = 3
        carousel.get_nth_page.return_value = page
        back_btn = mock.Mock()
        next_btn = mock.Mock()
        ready_window = types.SimpleNamespace(
            _nav_ready=True,
            carousel=carousel,
            back_btn=back_btn,
            next_btn=next_btn,
            is_oobe=True,
            _content_page=lambda clamp: clamp,
        )

        oobe.OobeWindow._update_nav_buttons(ready_window)

        carousel.set_interactive.assert_called_once_with(False)
        back_btn.set_visible.assert_called_once_with(True)
        next_btn.set_visible.assert_called_once_with(False)
        next_btn.set_label.assert_called_once_with(oobe._("Next →"))
        next_btn.remove_css_class.assert_called_once_with("suggested-action")

    def test_continue_offline_physically_removes_online_pages(self):
        class Page:
            def __init__(self, requires_internet=False):
                self._requires_internet = requires_internet

        class Clamp:
            def __init__(self, page):
                self.page = page

            def get_child(self):
                return self.page

        class Carousel:
            def __init__(self, pages):
                self.pages = list(pages)

            def remove(self, page):
                self.pages.remove(page)

        local_before = Clamp(Page())
        update = Clamp(Page(requires_internet=True))
        local_after = Clamp(Page())
        apps = Clamp(Page(requires_internet=True))
        pages = [local_before, update, local_after, apps]
        events = []
        window = types.SimpleNamespace(
            offline_mode=False,
            _pages=list(pages),
            carousel=Carousel(pages),
            _content_page=lambda clamp: clamp.get_child(),
            _update_nav_buttons=lambda: events.append("updated"),
            _nav_next=lambda: events.append("next"),
        )

        oobe.OobeWindow._continue_offline(window)

        self.assertTrue(window.offline_mode)
        self.assertEqual(window._pages, [local_before, local_after])
        self.assertEqual(window.carousel.pages, [local_before, local_after])
        self.assertEqual(events, ["updated", "next"])


if __name__ == "__main__":
    unittest.main()
