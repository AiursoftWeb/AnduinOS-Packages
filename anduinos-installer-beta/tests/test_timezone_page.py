import dataclasses
import unittest
from unittest.mock import mock_open, patch

from helpers import valid_plan
from installer_core.validation import validate_plan
from pages import _load_timezones


class TimezonePageTests(unittest.TestCase):
    def test_utc_is_first_even_when_zone_tab_does_not_list_it(self):
        zone_tab = (
            "# country-code coordinates TZ comments\n"
            "CN\t+3114+12128\tAsia/Shanghai\n"
            "US\t+404251-0740023\tAmerica/New_York\n"
        )
        with patch("builtins.open", mock_open(read_data=zone_tab)):
            self.assertEqual(
                _load_timezones(),
                ["UTC", "America/New_York", "Asia/Shanghai"],
            )

    def test_utc_is_not_duplicated_if_zone_tab_lists_it(self):
        zone_tab = "ZZ\t+0000+00000\tUTC\n"
        with patch("builtins.open", mock_open(read_data=zone_tab)):
            self.assertEqual(_load_timezones(), ["UTC"])

    def test_utc_is_a_valid_installation_timezone(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            regional=dataclasses.replace(plan.regional, timezone="UTC"),
        )
        validate_plan(plan)


if __name__ == "__main__":
    unittest.main()
