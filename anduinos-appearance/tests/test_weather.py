from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP_SOURCE = SRC / "anduinos-appearance"
PACKAGES_ROOT = ROOT.parent
sys.path.insert(0, str(SRC))

from anduinos_appearance import weather  # noqa: E402


ZONE_TAB = """\
CN\t+3114+12128\tAsia/Shanghai\tBeijing Time
CN\t+4348+08735\tAsia/Urumqi\tXinjiang Time
GB\t+513030-0000731\tEurope/London
"""


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class WeatherTests(unittest.TestCase):
    def zone_tab(self):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "zone.tab"
        path.write_text(ZONE_TAB, encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_zone_tab_coordinate_formats(self):
        self.assertEqual(
            weather.parse_zone_coordinates("+3114+12128"),
            (31 + 14 / 60, 121 + 28 / 60),
        )
        latitude, longitude = weather.parse_zone_coordinates("+513030-0000731")
        self.assertAlmostEqual(latitude, 51 + 30 / 60 + 30 / 3600)
        self.assertAlmostEqual(longitude, -(7 / 60 + 31 / 3600))

    def test_timezone_uses_iana_representative_city_without_network(self):
        location = weather.location_from_timezone("Asia/Shanghai", self.zone_tab())

        self.assertEqual(location.name, "Shanghai")
        self.assertEqual(location.country_code, "CN")
        self.assertAlmostEqual(location.latitude, 31 + 14 / 60)
        self.assertAlmostEqual(location.longitude, 121 + 28 / 60)

    def test_utc_and_unknown_timezones_fall_back_to_london(self):
        for timezone in ("Etc/UTC", "UTC", "Invalid/Timezone"):
            with self.subTest(timezone=timezone):
                location = weather.location_from_timezone(timezone, self.zone_tab())
                self.assertEqual(location.name, "London")
                self.assertEqual(location.country_code, "GB")

    def test_ip_lookup_uses_one_https_request_and_minimal_result(self):
        response = FakeResponse(json.dumps({
            "ip": "192.0.2.1",
            "city": "Suzhou",
            "country_code": "CN",
            "latitude": 31.3,
            "longitude": 120.6,
            "postal": "discarded",
            "org": "discarded",
        }).encode())
        with mock.patch.object(weather, "urlopen", return_value=response) as urlopen:
            location = weather.locate_by_ip()

        self.assertEqual(
            location,
            weather.WeatherLocation("Suzhou", 31.3, 120.6, "CN"),
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://ipapi.co/json/")
        self.assertEqual(urlopen.call_count, 1)

    def test_invalid_ip_location_response_is_rejected(self):
        for payload in (
            {"city": "", "country_code": "CN", "latitude": 31, "longitude": 120},
            {"city": "Suzhou", "country_code": "CN", "latitude": 91, "longitude": 120},
            {"city": "Suzhou", "country_code": "CN", "latitude": 31, "longitude": 181},
        ):
            with self.subTest(payload=payload):
                response = FakeResponse(json.dumps(payload).encode())
                with mock.patch.object(weather, "urlopen", return_value=response):
                    with self.assertRaises(ValueError):
                        weather.locate_by_ip()

    def test_network_failure_uses_offline_timezone_fallback(self):
        fallback = weather.WeatherLocation("London", 51.5, -0.12, "GB")
        with (
            mock.patch.object(weather, "locate_by_ip", side_effect=TimeoutError),
            mock.patch.object(weather, "location_from_timezone", return_value=fallback),
        ):
            location, used_network = weather.prepare_weather_location()

        self.assertEqual(location, fallback)
        self.assertFalse(used_network)

    def test_location_is_written_in_simpleweather_json_format(self):
        location = weather.WeatherLocation("Xi'an", 34.26, 108.94, "CN")
        with mock.patch.object(weather.subprocess, "run") as run:
            weather.write_weather_location(location)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertEqual(
            commands[0],
            [
                "dconf",
                "write",
                f"{weather.WEATHER_DCONF}/my-loc-provider",
                "'disable'",
            ],
        )
        self.assertEqual(
            commands[1],
            [
                "dconf",
                "write",
                f"{weather.WEATHER_DCONF}/weather-provider",
                "'open-meteo'",
            ],
        )
        self.assertEqual(commands[2][:3], ["dconf", "write", f"{weather.WEATHER_DCONF}/locations"])
        stored = json.loads(commands[2][3])
        self.assertEqual(len(stored), 1)
        self.assertEqual(
            json.loads(stored[0]),
            {"name": "Xi'an", "lat": 34.26, "lon": 108.94, "cc": "cn"},
        )
        self.assertEqual(
            commands[3],
            ["dconf", "write", f"{weather.WEATHER_DCONF}/main-location-index", "int64 0"],
        )

    def test_consent_is_recorded_and_can_be_revoked(self):
        with mock.patch.object(weather.subprocess, "run") as run:
            weather.record_weather_consent()
            weather.revoke_weather_consent()

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["dconf", "write", weather.CONSENT_KEY, "uint32 1"],
                ["dconf", "reset", weather.CONSENT_KEY],
            ],
        )

    def test_ui_requires_combined_consent_before_location_setup(self):
        source = APP_SOURCE.read_text(encoding="utf-8")

        self.assertIn("Weather Services Privacy Agreement", source)
        self.assertIn("Approximate location (ipapi.co)", source)
        self.assertIn("Weather data (Open-Meteo)", source)
        self.assertIn("Agree to All", source)
        self.assertIn("Decline", source)
        self.assertIn("if response_id == 'accept':\n                on_accept()", source)
        self.assertIn("lambda: self._reject_weather_enable(switch_row)", source)
        self.assertIn("find-location-symbolic", source)

    def test_package_keeps_upstream_activation_but_has_no_fixed_city(self):
        defaults = (
            PACKAGES_ROOT
            / "gnome-shell-extension-simple-weather/dconf/18-simple-weather.conf"
        ).read_text(encoding="utf-8")
        project = (ROOT / "anduinos-appearance.aosproj").read_text(encoding="utf-8")

        self.assertIn("is-activated=true", defaults)
        self.assertIn("my-loc-provider='disable'", defaults)
        self.assertNotIn("locations=", defaults)
        self.assertIn(
            'IncludeFile Include="src/anduinos_appearance/weather.py"', project
        )


if __name__ == "__main__":
    unittest.main()
