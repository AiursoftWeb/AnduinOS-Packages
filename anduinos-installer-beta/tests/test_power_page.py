import unittest
from pathlib import Path
from unittest.mock import patch

from installer_core.power import PowerProbeResult
from pages import (
    _replace_navigation_page,
    build_post_welcome_page,
    confirm_low_battery_override,
    low_battery_warning_needed,
    recheck_power_requirement,
)


class PowerPageRoutingTests(unittest.TestCase):
    def low(self):
        return PowerProbeResult(25, True, 1)

    def safe(self):
        return PowerProbeResult(26, True, 1)

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

    def test_safe_or_missing_battery_hides_power_page_and_preserves_network_route(self):
        for power in (
            self.safe(),
            PowerProbeResult(None, False, 0),
        ):
            with self.subTest(power=power):
                shared = {"development_mode": True}
                nav = object()
                network_page = object()
                with (
                    patch("pages.build_low_battery_page") as low,
                    patch(
                        "pages.build_network_page", return_value=network_page
                    ) as network,
                ):
                    result = build_post_welcome_page(
                        shared, nav, result=power
                    )
                self.assertIs(result, network_page)
                low.assert_not_called()
                network.assert_called_once_with(shared, nav)

    def test_full_connectivity_still_skips_network_after_power_check(self):
        shared = {"development_mode": False}
        nav = object()
        keyboard_page = object()
        with (
            patch("pages.should_show_network_page", return_value=False),
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
                "pages.build_network_page", return_value=network_page
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

    def test_continuing_replaces_the_low_battery_page_in_the_stack(self):
        welcome = object()
        low_battery = object()
        network = object()

        class Stack:
            def __init__(self, pages):
                self.pages = pages

            def get_n_items(self):
                return len(self.pages)

            def get_item(self, index):
                return self.pages[index]

        class Navigation:
            def __init__(self):
                self.stack = Stack([welcome, low_battery])
                self.replacement = None

            def get_navigation_stack(self):
                return self.stack

            def replace(self, pages):
                self.replacement = pages

        navigation = Navigation()
        _replace_navigation_page(navigation, low_battery, network)
        self.assertEqual(navigation.replacement, [welcome, network])

    def test_low_battery_ui_requires_checkbox_before_continue(self):
        source = Path("src/pages.py").read_text(encoding="utf-8")
        page_source = source.split("def build_low_battery_page", 1)[1].split(
            "# ── page 2:", 1
        )[0]
        self.assertIn('page.set_tag("low-battery")', page_source)
        self.assertIn("next_sensitive=False", page_source)
        self.assertIn("risk_confirmation.get_active()", page_source)
        self.assertIn("recheck_power_requirement(shared)", page_source)
        self.assertIn("_replace_navigation_page(", page_source)


if __name__ == "__main__":
    unittest.main()
