"""Privacy-preserving location setup for the SimpleWeather extension."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen


IP_LOCATION_URL = "https://ipapi.co/json/"
WEATHER_DCONF = "/org/gnome/shell/extensions/simple-weather"
CONSENT_KEY = "/com/anduinos/appearance/weather-consent-version"
CONSENT_VERSION = 1
ZONE_TAB = Path("/usr/share/zoneinfo/zone.tab")


@dataclass(frozen=True)
class WeatherLocation:
    name: str
    latitude: float
    longitude: float
    country_code: str

    def extension_value(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "lat": self.latitude,
                "lon": self.longitude,
                "cc": self.country_code.lower(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _coordinate(component: str, degree_digits: int) -> float:
    sign = -1 if component[0] == "-" else 1
    digits = component[1:]
    degrees = int(digits[:degree_digits])
    minutes = int(digits[degree_digits:degree_digits + 2])
    seconds = int(digits[degree_digits + 2:]) if len(digits) > degree_digits + 2 else 0
    return sign * (degrees + minutes / 60 + seconds / 3600)


def parse_zone_coordinates(value: str) -> tuple[float, float]:
    match = re.fullmatch(r"([+-](?:\d{4}|\d{6}))([+-](?:\d{5}|\d{7}))", value)
    if match is None:
        raise ValueError(f"Invalid zone.tab coordinates: {value}")
    return _coordinate(match.group(1), 2), _coordinate(match.group(2), 3)


def _zone_location(timezone: str, zone_tab: Path) -> WeatherLocation | None:
    try:
        lines = zone_tab.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 3 or fields[2] != timezone:
            continue
        latitude, longitude = parse_zone_coordinates(fields[1])
        return WeatherLocation(
            name=timezone.rsplit("/", 1)[-1].replace("_", " "),
            latitude=latitude,
            longitude=longitude,
            country_code=fields[0].split(",", 1)[0],
        )
    return None


def system_timezone(timezone_file: Path = Path("/etc/timezone")) -> str:
    try:
        timezone = timezone_file.read_text(encoding="utf-8").strip()
        if timezone:
            return timezone
    except OSError:
        pass

    try:
        localtime = Path("/etc/localtime").resolve()
        return str(localtime.relative_to("/usr/share/zoneinfo"))
    except (OSError, ValueError):
        return "Etc/UTC"


def location_from_timezone(
    timezone: str | None = None,
    zone_tab: Path = ZONE_TAB,
) -> WeatherLocation:
    timezone = timezone or system_timezone()
    location = _zone_location(timezone, zone_tab)
    if location is not None:
        return location

    london = _zone_location("Europe/London", zone_tab)
    if london is not None:
        return london
    return WeatherLocation("London", 51.508333, -0.125278, "gb")


def locate_by_ip(timeout: float = 10.0) -> WeatherLocation:
    request = Request(
        IP_LOCATION_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "AnduinOS-Appearance/2.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)

    name = payload.get("city")
    country_code = payload.get("country_code") or payload.get("country")
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Location service did not return a city")
    if not isinstance(country_code, str) or len(country_code) != 2:
        raise ValueError("Location service did not return a country code")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        raise ValueError("Location service did not return numeric coordinates")
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Location service returned an invalid latitude")
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Location service returned an invalid longitude")

    return WeatherLocation(name.strip(), latitude, longitude, country_code)


def prepare_weather_location() -> tuple[WeatherLocation, bool]:
    """Try one consented network lookup, falling back to local timezone data."""
    try:
        return locate_by_ip(), True
    except Exception:
        return location_from_timezone(), False


def write_weather_location(location: WeatherLocation) -> None:
    locations = json.dumps(
        [location.extension_value()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Keep every network request inside the consent shown by Appearance.  In
    # particular, do not let SimpleWeather reuse an earlier OpenWeatherMap or
    # automatic-location choice after the user has only approved Open-Meteo
    # and this one-shot ipapi.co lookup.
    subprocess.run(
        [
            "dconf",
            "write",
            f"{WEATHER_DCONF}/my-loc-provider",
            "'disable'",
        ],
        check=True,
    )
    subprocess.run(
        [
            "dconf",
            "write",
            f"{WEATHER_DCONF}/weather-provider",
            "'open-meteo'",
        ],
        check=True,
    )
    subprocess.run(
        ["dconf", "write", f"{WEATHER_DCONF}/locations", locations],
        check=True,
    )
    subprocess.run(
        ["dconf", "write", f"{WEATHER_DCONF}/main-location-index", "int64 0"],
        check=True,
    )


def record_weather_consent() -> None:
    subprocess.run(
        ["dconf", "write", CONSENT_KEY, f"uint32 {CONSENT_VERSION}"],
        check=True,
    )


def revoke_weather_consent() -> None:
    subprocess.run(["dconf", "reset", CONSENT_KEY], check=True)
