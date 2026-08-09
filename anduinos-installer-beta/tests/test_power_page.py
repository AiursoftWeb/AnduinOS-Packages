import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.power import PowerProbeResult
from installer_core.probe import PlatformProbe
from pages import (
    _planned_page_route,
    build_post_welcome_page,
    confirm_low_battery_override,
    low_battery_warning_needed,
    reboot_to_firmware_settings,
    recheck_power_requirement,
    secure_boot_recommendation_needed,
)


class PowerPageRoutingTests(unittest.TestCase):
    def low(self):
        return PowerProbeResult(25, True, 1)

    def safe(self):
        return PowerProbeResult(26, True, 1)

    def platform(self, secure_boot):
        return PlatformProbe(
            Architecture.AMD64,
            Firmware.UEFI,
            secure_boot,
        )

    def test_low_battery_page_precedes_network_page(self):
        shared = {}
        nav = object()
        low_page = object()
        with (
            patch("pages.build_low_battery_page", return_value=low_page) as low,
            patch("pages.build_network_page") as network,
            patch("pages.build_keyboard_page") as keyboard,
        ):
            result = build_post_welcome_page(shared, nav, result=self.low())
        self.assertIs(result, low_page)
        low.assert_called_once_with(shared, nav, self.low())
        network.assert_not_called()
        keyboard.assert_not_called()

    def test_safe_or_missing_battery_routes_through_secure_boot_page(self):
        for power in (
            self.safe(),
            PowerProbeResult(None, False, 0),
        ):
            with self.subTest(power=power):
                shared = {"development_mode": False}
                nav = object()
                network_page = object()
                with (
                    patch("pages.build_low_battery_page") as low,
                    patch(
                        "pages.probe_platform",
                        return_value=self.platform(SecureBoot.DISABLED),
                    ),
                    patch(
                        "pages.build_secure_boot_page",
                        return_value=network_page,
                    ) as secure_boot,
                ):
                    result = build_post_welcome_page(
                        shared, nav, result=power
                    )
                self.assertIs(result, network_page)
                low.assert_not_called()
                secure_boot.assert_called_once_with(shared, nav)

    def test_development_mode_always_previews_low_battery_page_first(self):
        shared = {"development_mode": True}
        nav = object()
        low_page = object()
        safe_power = PowerProbeResult(None, False, 0)
        with (
            patch(
                "pages.build_low_battery_page", return_value=low_page
            ) as low,
            patch("pages.build_secure_boot_page") as secure_boot,
        ):
            result = build_post_welcome_page(
                shared, nav, result=safe_power
            )
        self.assertIs(result, low_page)
        low.assert_called_once_with(shared, nav, safe_power)
        secure_boot.assert_not_called()

    def test_full_connectivity_still_skips_network_after_power_check(self):
        shared = {"development_mode": False}
        nav = object()
        keyboard_page = object()
        with (
            patch("pages.should_show_network_page", return_value=False),
            patch(
                "pages.probe_platform",
                return_value=self.platform(SecureBoot.ENABLED),
            ),
            patch("pages.build_low_battery_page") as low,
            patch("pages.build_network_page") as network,
            patch(
                "pages.build_keyboard_page", return_value=keyboard_page
            ) as keyboard,
        ):
            result = build_post_welcome_page(
                shared, nav, result=self.safe()
            )
        self.assertIs(result, keyboard_page)
        low.assert_not_called()
        network.assert_not_called()
        keyboard.assert_called_once_with(shared, nav)

    def test_secure_boot_recommendation_routing(self):
        with patch("pages.probe_platform") as probe:
            self.assertFalse(
                secure_boot_recommendation_needed(
                    {}, self.platform(SecureBoot.ENABLED)
                )
            )
            self.assertTrue(
                secure_boot_recommendation_needed(
                    {}, self.platform(SecureBoot.DISABLED)
                )
            )
            self.assertTrue(
                secure_boot_recommendation_needed(
                    {}, self.platform(SecureBoot.UNSUPPORTED)
                )
            )
            self.assertTrue(
                secure_boot_recommendation_needed(
                    {"development_mode": True},
                    self.platform(SecureBoot.ENABLED),
                )
            )
        probe.assert_not_called()

    def test_firmware_reboot_uses_systemd_firmware_setup_request(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        succeeded, error = reboot_to_firmware_settings(run)
        self.assertTrue(succeeded)
        self.assertEqual(error, "")
        self.assertEqual(
            calls[0][0], ["systemctl", "reboot", "--firmware-setup"]
        )

    def test_firmware_reboot_reports_systemd_failure(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "not supported")

        succeeded, error = reboot_to_firmware_settings(run)
        self.assertFalse(succeeded)
        self.assertEqual(error, "not supported")

    def test_risk_override_requires_confirmation_and_is_session_only(self):
        first_session = {}
        self.assertTrue(low_battery_warning_needed(first_session, self.low()))
        self.assertFalse(
            confirm_low_battery_override(first_session, confirmed=False)
        )
        self.assertTrue(low_battery_warning_needed(first_session, self.low()))
        self.assertTrue(
            confirm_low_battery_override(first_session, confirmed=True)
        )
        self.assertFalse(low_battery_warning_needed(first_session, self.low()))
        self.assertTrue(low_battery_warning_needed({}, self.low()))

    def test_override_hides_warning_when_route_is_visited_again(self):
        shared = {"development_mode": True}
        confirm_low_battery_override(shared, True)
        nav = object()
        network_page = object()
        with (
            patch("pages.build_low_battery_page") as low,
            patch(
                "pages.build_secure_boot_page", return_value=network_page
            ),
        ):
            result = build_post_welcome_page(
                shared, nav, result=self.low()
            )
        self.assertIs(result, network_page)
        low.assert_not_called()

    def test_recheck_allows_progress_after_power_improves(self):
        shared = {}
        result, warning_needed = recheck_power_requirement(
            shared, power_probe=self.safe
        )
        self.assertEqual(result, self.safe())
        self.assertFalse(warning_needed)
        self.assertIs(shared["_power_probe_result"], result)

    def test_low_battery_ui_requires_checkbox_before_continue(self):
        source = Path("src/pages.py").read_text(encoding="utf-8")
        page_source = source.split("def build_low_battery_page", 1)[1].split(
            "# ── page 2:", 1
        )[0]
        self.assertIn('page.set_tag("low-battery")', page_source)
        self.assertIn("next_sensitive=False", page_source)
        self.assertIn("risk_confirmation.get_active()", page_source)
        self.assertIn("recheck_power_requirement(shared)", page_source)
        self.assertIn("nav_view.push(", page_source)
        self.assertIn("safe = not current.requires_warning", page_source)
        self.assertIn('risk_confirmation.set_visible(not safe)', page_source)
        self.assertIn('"emblem-ok-symbolic" if safe', page_source)
        self.assertIn("_start_power_auto_refresh(page, _on_recheck)", page_source)

    def test_power_page_listens_to_upower_and_has_minute_fallback(self):
        source = Path("src/pages.py").read_text(encoding="utf-8")
        monitor_source = source.split(
            "def _start_power_auto_refresh", 1
        )[1].split("def _build_network_or_keyboard_page", 1)[0]
        self.assertIn('GLib.timeout_add_seconds(60, _refresh_timer)', monitor_source)
        self.assertIn("return True", monitor_source)
        self.assertIn('"org.freedesktop.UPower"', monitor_source)
        self.assertIn('"EnumerateDevices"', monitor_source)
        self.assertIn('"g-properties-changed"', monitor_source)
        self.assertIn('page.connect("map", _start)', monitor_source)
        self.assertIn('page.connect("unmap", _stop)', monitor_source)
        self.assertIn("proxy.disconnect(handler)", monitor_source)

    def test_secure_boot_actions_do_not_replace_wizard_navigation(self):
        source = Path("src/pages.py").read_text(encoding="utf-8")
        page_source = source.split("def build_secure_boot_page", 1)[1].split(
            "# ── page 2:", 1
        )[0]
        self.assertIn("page_actions.append(restart_button)", page_source)
        self.assertIn("page_actions.append(skip_button)", page_source)
        self.assertIn("on_back=lambda: nav_view.pop()", page_source)
        self.assertIn("on_next=_continue", page_source)
        self.assertIn("next_label=_SECURE_BOOT_SKIP_LABEL", page_source)
        self.assertIn(
            'navigation.next_button.remove_css_class("suggested-action")',
            page_source,
        )
        self.assertNotIn("navigation.set_start_widget", page_source)
        self.assertNotIn("restart_button.set_sensitive(False)", page_source)
        self.assertNotIn("development protection mode", page_source)

    def test_page_route_has_one_entry_per_real_normal_page(self):
        shared = {
            "_page_route_initialized": True,
            "_power_probe_result": self.safe(),
            "_platform_probe_result": self.platform(SecureBoot.ENABLED),
            "_network_page_planned": False,
            "storage_strategy": "erase-btrfs",
        }
        self.assertEqual(
            _planned_page_route(shared),
            (
                "welcome",
                "keyboard",
                "software",
                "disk",
                "storage-strategy",
                "user",
                "timezone",
                "summary",
                "progress",
            ),
        )

    def test_page_route_includes_each_selected_conditional_page(self):
        shared = {
            "development_mode": True,
            "_page_route_initialized": True,
            "_power_probe_result": self.safe(),
            "_platform_probe_result": self.platform(SecureBoot.ENABLED),
            "_network_page_planned": True,
            "storage_strategy": "advanced-coexistence",
        }
        route = _planned_page_route(shared)
        self.assertEqual(len(route), 13)
        self.assertEqual(route[:4], (
            "welcome",
            "low-battery",
            "secure-boot-recommendation",
            "network",
        ))
        self.assertEqual(route[8], "advanced-storage")


if __name__ == "__main__":
    unittest.main()
