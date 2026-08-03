"""Read-only Wi-Fi discovery for the unprivileged installer frontend."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Callable


@dataclass(frozen=True)
class WifiNetwork:
    """One visible NetworkManager access point, grouped by SSID."""

    ssid: str
    signal: int
    security: str
    active: bool = False


def split_nmcli_terse(line: str) -> tuple[str, ...]:
    """Split an escaped ``nmcli --terse`` record without losing colons."""

    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line.rstrip("\n"):
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return tuple(fields)


def parse_wifi_networks(output: str) -> tuple[WifiNetwork, ...]:
    """Parse, de-duplicate and rank NetworkManager's visible networks."""

    networks: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        fields = split_nmcli_terse(line)
        if len(fields) != 4:
            continue
        active, ssid, signal_text, security = fields
        ssid = ssid.strip()
        if not ssid:
            continue
        try:
            signal = max(0, min(100, int(signal_text)))
        except ValueError:
            signal = 0
        candidate = WifiNetwork(
            ssid=ssid,
            signal=signal,
            security=security.strip() or "--",
            active=active.strip() == "*",
        )
        previous = networks.get(ssid)
        if previous is None or (candidate.active, candidate.signal) > (
            previous.active,
            previous.signal,
        ):
            networks[ssid] = candidate
    return tuple(
        sorted(
            networks.values(),
            key=lambda network: (
                not network.active,
                -network.signal,
                network.ssid.casefold(),
            ),
        )
    )


def scan_wifi_networks(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[WifiNetwork, ...]:
    """Ask NetworkManager for access points without changing connections."""

    result = run(
        (
            "nmcli",
            "--terse",
            "--escape",
            "yes",
            "--fields",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes",
        ),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "NetworkManager could not scan Wi-Fi")
    return parse_wifi_networks(result.stdout)


def wifi_radio_enabled(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Return NetworkManager's current Wi-Fi radio state."""

    result = run(
        ("nmcli", "radio", "wifi"),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "Could not read the Wi-Fi radio state")
    state = result.stdout.strip().lower()
    if state == "enabled":
        return True
    if state == "disabled":
        return False
    raise RuntimeError(f"Unknown Wi-Fi radio state: {state or 'empty'}")


def set_wifi_radio(
    enabled: bool,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Enable or disable Wi-Fi through NetworkManager."""

    result = run(
        ("nmcli", "radio", "wifi", "on" if enabled else "off"),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "Could not change the Wi-Fi radio state")
