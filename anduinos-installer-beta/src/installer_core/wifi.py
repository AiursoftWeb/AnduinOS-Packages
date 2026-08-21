"""Native NetworkManager Wi-Fi support for the installer frontend.

Discovery, radio control, activation and disconnection all use libnm directly.
Connection profiles and their secrets are built in memory and sent to
NetworkManager over D-Bus, so credentials never appear in a process argument
list and no desktop control-center or external authentication dialog is
required.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import threading
import time
from typing import Mapping
import uuid as uuid_module


class WifiError(RuntimeError):
    """A user-visible NetworkManager operation failure."""


class WifiCancelled(WifiError):
    """The current Wi-Fi operation was cancelled by the user."""


class WifiSecurity(str, Enum):
    OPEN = "open"
    OWE = "owe"
    WEP = "wep"
    PERSONAL = "personal"
    ENTERPRISE = "enterprise"


class WifiEapMethod(str, Enum):
    PEAP = "peap"
    TTLS = "ttls"
    TLS = "tls"
    PWD = "pwd"


@dataclass(frozen=True)
class WifiNetwork:
    """One visible NetworkManager access point, grouped by SSID."""

    ssid: str
    signal: int
    security: str
    active: bool = False
    bssid: str = ""
    device: str = ""
    security_kind: WifiSecurity = WifiSecurity.OPEN
    wps_pbc: bool = False
    wps_pin: bool = False


@dataclass(frozen=True)
class WifiProfile:
    """A persistent NetworkManager Wi-Fi connection profile."""

    uuid: str
    name: str
    ssid: str
    key_management: str = ""
    eap_method: str = ""
    active: bool = False
    device: str = ""


@dataclass(frozen=True)
class WifiConnectionRequest:
    """Declarative credentials for one internal Wi-Fi connection attempt."""

    ssid: str
    security: WifiSecurity
    device: str = ""
    bssid: str = ""
    security_label: str = ""
    hidden: bool = False
    password: str = ""
    identity: str = ""
    anonymous_identity: str = ""
    eap_method: WifiEapMethod = WifiEapMethod.PEAP
    phase2_auth: str = "mschapv2"
    ca_certificate: str = ""
    domain_suffix_match: str = ""
    client_certificate: str = ""
    private_key: str = ""
    private_key_password: str = ""


def split_nmcli_terse(line: str) -> tuple[str, ...]:
    """Split an escaped nmcli terse record without losing literal colons."""

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


def classify_wifi_security(
    security: str, wpa_flags: str = "", rsn_flags: str = ""
) -> WifiSecurity:
    """Classify NetworkManager's display fields into a connection workflow."""

    description = " ".join((security, wpa_flags, rsn_flags)).casefold()
    if "802.1x" in description or "802_1x" in description or "eap" in description:
        return WifiSecurity.ENTERPRISE
    if "owe" in description:
        return WifiSecurity.OWE
    if "wep" in description:
        return WifiSecurity.WEP
    if "wpa" in description or "psk" in description or "sae" in description:
        return WifiSecurity.PERSONAL
    return WifiSecurity.OPEN


def parse_wifi_networks(
    output: str,
    wps_support: Mapping[tuple[str, str], tuple[bool, bool]] | None = None,
) -> tuple[WifiNetwork, ...]:
    """Parse, de-duplicate and rank NetworkManager's visible networks."""

    wps_support = wps_support or {}
    networks: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        fields = split_nmcli_terse(line)
        if len(fields) == 4:
            # Compatibility with older test fixtures and recorded scans.
            active, ssid, signal_text, security = fields
            bssid = device = wpa_flags = rsn_flags = ""
        elif len(fields) == 8:
            (
                active,
                ssid,
                bssid,
                signal_text,
                security,
                device,
                wpa_flags,
                rsn_flags,
            ) = fields
        else:
            continue
        ssid = ssid.strip()
        if not ssid:
            continue
        try:
            signal = max(0, min(100, int(signal_text)))
        except ValueError:
            signal = 0
        wps_pbc, wps_pin = wps_support.get(
            (device, bssid.upper()), (False, False)
        )
        candidate = WifiNetwork(
            ssid=ssid,
            signal=signal,
            security=security.strip() or "--",
            active=active.strip() == "*",
            bssid=bssid.upper(),
            device=device,
            security_kind=classify_wifi_security(
                security, wpa_flags, rsn_flags
            ),
            wps_pbc=wps_pbc,
            wps_pin=wps_pin,
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


def _load_libnm():
    try:
        import gi

        gi.require_version("NM", "1.0")
        from gi.repository import Gio, GLib, GObject, NM
    except (ImportError, ValueError) as error:
        raise WifiError("NetworkManager's libnm bindings are unavailable") from error
    return Gio, GLib, GObject, NM


@contextmanager
def _nm_client_context(existing_client=None):
    """Own a private GLib context for a background libnm client.

    Network discovery runs outside GTK's main thread.  Inheriting GTK's
    already-owned default context makes libnm initially report the Wi-Fi radio
    as disabled, so every short-lived client receives its own context.
    """

    _gio, GLib, _gobject, NM = _load_libnm()
    context = GLib.MainContext.new()
    context.push_thread_default()
    try:
        client = existing_client or NM.Client.new(None)
        yield client, NM, context
    finally:
        context.pop_thread_default()


def _security_from_access_point(access_point, NM) -> tuple[str, WifiSecurity]:
    """Translate libnm capability bits into a label and connection workflow."""

    security_flags = getattr(NM, "80211ApSecurityFlags")
    ap_flags = getattr(NM, "80211ApFlags")
    wpa = int(access_point.get_wpa_flags())
    rsn = int(access_point.get_rsn_flags())
    combined = wpa | rsn
    enterprise_mask = int(security_flags.KEY_MGMT_802_1X) | int(
        security_flags.KEY_MGMT_EAP_SUITE_B_192
    )
    owe_mask = int(security_flags.KEY_MGMT_OWE) | int(
        security_flags.KEY_MGMT_OWE_TM
    )
    personal_mask = int(security_flags.KEY_MGMT_PSK) | int(
        security_flags.KEY_MGMT_SAE
    )
    if combined & enterprise_mask:
        label = (
            "WPA3 Enterprise"
            if combined & int(security_flags.KEY_MGMT_EAP_SUITE_B_192)
            else "WPA2 Enterprise"
        )
        return label, WifiSecurity.ENTERPRISE
    if combined & owe_mask:
        return "OWE", WifiSecurity.OWE
    if combined & personal_mask:
        versions = []
        if wpa & int(security_flags.KEY_MGMT_PSK):
            versions.append("WPA")
        if rsn & int(security_flags.KEY_MGMT_PSK):
            versions.append("WPA2")
        if rsn & int(security_flags.KEY_MGMT_SAE):
            versions.append("WPA3")
        return " ".join(versions) or "WPA", WifiSecurity.PERSONAL
    if int(access_point.get_flags()) & int(ap_flags.PRIVACY):
        return "WEP", WifiSecurity.WEP
    return "--", WifiSecurity.OPEN


def _networks_from_client(client, NM) -> tuple[WifiNetwork, ...]:
    """Build de-duplicated UI models from libnm's access-point cache."""

    ap_flags = getattr(NM, "80211ApFlags")
    networks: dict[str, WifiNetwork] = {}
    for device in client.get_devices():
        if device.get_device_type() != NM.DeviceType.WIFI:
            continue
        active_ap = device.get_active_access_point()
        active_path = active_ap.get_path() if active_ap is not None else ""
        for access_point in device.get_access_points():
            ssid_bytes = access_point.get_ssid()
            if ssid_bytes is None:
                continue
            try:
                ssid = NM.utils_ssid_to_utf8(ssid_bytes.get_data())
            except Exception:
                continue
            if not ssid:
                continue
            security, security_kind = _security_from_access_point(
                access_point, NM
            )
            flags = int(access_point.get_flags())
            candidate = WifiNetwork(
                ssid=ssid,
                signal=max(0, min(100, int(access_point.get_strength()))),
                security=security,
                active=access_point.get_path() == active_path,
                bssid=(access_point.get_bssid() or "").upper(),
                device=device.get_iface() or "",
                security_kind=security_kind,
                wps_pbc=bool(flags & int(ap_flags.WPS_PBC)),
                wps_pin=bool(flags & int(ap_flags.WPS_PIN)),
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
    *, rescan: bool = True, _client=None
) -> tuple[WifiNetwork, ...]:
    """Read visible access points directly from NetworkManager through libnm."""

    _gio, GLib, _gobject, NM = _load_libnm()
    context = GLib.MainContext.new()
    context.push_thread_default()
    try:
        client = _client or NM.Client.new(None)
        devices = [
            device
            for device in client.get_devices()
            if device.get_device_type() == NM.DeviceType.WIFI
        ]
        if rescan and devices:
            previous_scans = {
                device.get_path(): device.get_last_scan() for device in devices
            }
            requested = []
            errors = []
            for device in devices:
                try:
                    if device.request_scan(None):
                        requested.append(device)
                except Exception as error:
                    errors.append(error)
            if not requested and errors:
                raise WifiError(f"NetworkManager could not scan Wi-Fi: {errors[0]}")
            deadline = time.monotonic() + 12
            while requested and time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if all(
                    device.get_last_scan() != previous_scans[device.get_path()]
                    for device in requested
                ):
                    break
                time.sleep(0.05)
        return _networks_from_client(client, NM)
    except WifiError:
        raise
    except Exception as error:
        raise WifiError(f"NetworkManager could not scan Wi-Fi: {error}") from error
    finally:
        context.pop_thread_default()


def wifi_radio_enabled(*, _client=None) -> bool:
    """Return NetworkManager's current Wi-Fi radio state through libnm."""

    try:
        with _nm_client_context(_client) as (client, _NM, _context):
            return bool(client.wireless_get_enabled())
    except Exception as error:
        raise WifiError(f"Could not read the Wi-Fi radio state: {error}") from error


def set_wifi_radio(enabled: bool, *, _client=None) -> None:
    """Enable or disable Wi-Fi directly through libnm."""

    try:
        with _nm_client_context(_client) as (client, _NM, _context):
            client.wireless_set_enabled(bool(enabled))
    except Exception as error:
        raise WifiError(f"Could not change the Wi-Fi radio state: {error}") from error


def disconnect_wifi(device: str, *, _client=None) -> None:
    """Disconnect one Wi-Fi adapter directly through libnm."""

    if not device or any(character.isspace() for character in device):
        raise WifiError("A valid Wi-Fi device is required")
    try:
        with _nm_client_context(_client) as (client, NM, _context):
            _find_wifi_device(client, NM, device).disconnect(None)
    except WifiError:
        raise
    except Exception as error:
        raise WifiError(f"Could not disconnect Wi-Fi: {error}") from error


def _validate_no_control_characters(value: str, field: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise WifiError(f"{field} contains unsupported control characters")


def validate_wifi_request(
    request: WifiConnectionRequest,
    *,
    wps: bool = False,
    existing_profile: bool = False,
) -> None:
    """Reject invalid or incomplete credentials before talking to NetworkManager."""

    if not request.ssid or len(request.ssid.encode("utf-8")) > 32:
        raise WifiError("Wi-Fi network names must contain 1 to 32 bytes")
    _validate_no_control_characters(request.ssid, "Wi-Fi network name")
    for value, field in (
        (request.password, "Wi-Fi password"),
        (request.identity, "Enterprise identity"),
        (request.anonymous_identity, "Anonymous identity"),
        (request.private_key_password, "Private-key password"),
    ):
        _validate_no_control_characters(value, field)
    if wps:
        if request.security is not WifiSecurity.PERSONAL:
            raise WifiError("WPS is available only for personal Wi-Fi networks")
        return
    if request.security is WifiSecurity.PERSONAL:
        is_hex_psk = bool(re.fullmatch(r"[0-9A-Fa-f]{64}", request.password))
        if not is_hex_psk and not 8 <= len(request.password) <= 63:
            raise WifiError("The Wi-Fi password must contain 8 to 63 characters")
    elif request.security is WifiSecurity.WEP:
        if not request.password:
            raise WifiError("A WEP key or passphrase is required")
    elif request.security is WifiSecurity.ENTERPRISE:
        if not request.identity and not existing_profile:
            raise WifiError("An enterprise identity is required")
        if request.eap_method is WifiEapMethod.TLS:
            if (
                not existing_profile
                and (not request.client_certificate or not request.private_key)
            ):
                raise WifiError(
                    "EAP-TLS requires a client certificate and private key"
                )
        elif not request.password:
            raise WifiError("An enterprise password is required")
    for certificate in (
        request.ca_certificate,
        request.client_certificate,
        request.private_key,
    ):
        if certificate and not Path(certificate).is_file():
            raise WifiError(f"Certificate or key file does not exist: {certificate}")


def _is_wpa3_only(label: str) -> bool:
    normalized = label.casefold()
    return "wpa3" in normalized and "wpa2" not in normalized


def build_nm_connection(
    request: WifiConnectionRequest,
    *,
    wps: bool = False,
    connection_uuid: str | None = None,
):
    """Build and verify an in-memory libnm connection containing all secrets."""

    validate_wifi_request(request, wps=wps)
    _gio, GLib, _gobject, NM = _load_libnm()
    connection = NM.SimpleConnection.new()

    setting_connection = NM.SettingConnection.new()
    setting_connection.props.id = f"AnduinOS · {request.ssid}"
    setting_connection.props.uuid = connection_uuid or str(uuid_module.uuid4())
    setting_connection.props.type = NM.SETTING_WIRELESS_SETTING_NAME
    setting_connection.props.autoconnect = True
    connection.add_setting(setting_connection)

    setting_wireless = NM.SettingWireless.new()
    setting_wireless.props.ssid = GLib.Bytes.new(request.ssid.encode("utf-8"))
    setting_wireless.props.mode = "infrastructure"
    setting_wireless.props.hidden = request.hidden
    connection.add_setting(setting_wireless)

    security = None
    if request.security is not WifiSecurity.OPEN:
        security = NM.SettingWirelessSecurity.new()
        if request.security is WifiSecurity.OWE:
            security.props.key_mgmt = "owe"
        elif request.security is WifiSecurity.WEP:
            security.props.key_mgmt = "none"
            security.props.wep_key0 = request.password
            security.props.wep_key_type = (
                NM.WepKeyType.KEY
                if re.fullmatch(r"(?:[0-9A-Fa-f]{10}|[0-9A-Fa-f]{26})", request.password)
                or len(request.password) in (5, 13)
                else NM.WepKeyType.PASSPHRASE
            )
        elif request.security is WifiSecurity.PERSONAL:
            security.props.key_mgmt = (
                "wpa-psk" if wps or not _is_wpa3_only(request.security_label) else "sae"
            )
            if wps:
                security.props.wps_method = NM.SettingWirelessSecurityWpsMethod.PBC
            else:
                security.props.psk = request.password
                security.props.wps_method = NM.SettingWirelessSecurityWpsMethod.DISABLED
        elif request.security is WifiSecurity.ENTERPRISE:
            security.props.key_mgmt = (
                "wpa-eap-suite-b-192"
                if "suite-b" in request.security_label.casefold()
                else "wpa-eap"
            )
        connection.add_setting(security)

    if request.security is WifiSecurity.ENTERPRISE:
        setting_8021x = NM.Setting8021x.new()
        setting_8021x.add_eap_method(request.eap_method.value)
        setting_8021x.props.identity = request.identity
        if request.anonymous_identity:
            setting_8021x.props.anonymous_identity = request.anonymous_identity
        if request.domain_suffix_match:
            setting_8021x.props.domain_suffix_match = request.domain_suffix_match
        if request.ca_certificate:
            if not setting_8021x.set_ca_cert(
                request.ca_certificate,
                NM.Setting8021xCKScheme.PATH,
                NM.Setting8021xCKFormat.UNKNOWN,
            ):
                raise WifiError("The CA certificate could not be loaded")
        if request.eap_method is WifiEapMethod.TLS:
            if not setting_8021x.set_client_cert(
                request.client_certificate,
                NM.Setting8021xCKScheme.PATH,
                NM.Setting8021xCKFormat.UNKNOWN,
            ):
                raise WifiError("The client certificate could not be loaded")
            if not setting_8021x.set_private_key(
                request.private_key,
                request.private_key_password,
                NM.Setting8021xCKScheme.PATH,
                NM.Setting8021xCKFormat.UNKNOWN,
            ):
                raise WifiError("The private key could not be loaded")
        else:
            setting_8021x.props.password = request.password
            if request.eap_method in (WifiEapMethod.PEAP, WifiEapMethod.TTLS):
                setting_8021x.props.phase2_auth = request.phase2_auth
        connection.add_setting(setting_8021x)

    ipv4 = NM.SettingIP4Config.new()
    ipv4.props.method = "auto"
    connection.add_setting(ipv4)
    ipv6 = NM.SettingIP6Config.new()
    ipv6.props.method = "auto"
    connection.add_setting(ipv6)

    try:
        connection.verify()
    except Exception as error:
        raise WifiError(f"Invalid Wi-Fi connection settings: {error}") from error
    return connection


_WPS_AGENT_CLASS = None


def _wps_agent_class(NM):
    """Create one libnm SecretAgent type that holds WPS enrollment open."""

    global _WPS_AGENT_CLASS
    if _WPS_AGENT_CLASS is not None:
        return _WPS_AGENT_CLASS

    class InstallerWpsAgent(NM.SecretAgentOld):
        __gtype_name__ = "AnduinInstallerWpsAgent"

        def __init__(self):
            super().__init__(
                identifier="com.anduinos.installer.wps",
                auto_register=False,
            )
            self.pending_requests = []

        def do_get_secrets(
            self,
            connection,
            connection_path,
            setting_name,
            hints,
            flags,
            callback,
            user_data,
        ):
            # NetworkManager cancels this pending request when the router
            # supplies WPS credentials. Returning early would abort WPS.
            self.pending_requests.append(
                (connection_path, setting_name, callback, user_data)
            )

        def do_cancel_get_secrets(self, connection_path, setting_name):
            self.pending_requests = [
                request
                for request in self.pending_requests
                if request[:2] != (connection_path, setting_name)
            ]

        def do_save_secrets(
            self, connection, connection_path, callback, user_data
        ):
            callback(self, connection, None, user_data)

        def do_delete_secrets(
            self, connection, connection_path, callback, user_data
        ):
            callback(self, connection, None, user_data)

    _WPS_AGENT_CLASS = InstallerWpsAgent
    return InstallerWpsAgent


def _find_wifi_device(client, NM, interface: str):
    wifi_devices = [
        device
        for device in client.get_devices()
        if device.get_device_type() == NM.DeviceType.WIFI
    ]
    if interface:
        for device in wifi_devices:
            if device.get_iface() == interface:
                return device
        raise WifiError(f"Wi-Fi adapter is no longer available: {interface}")
    if not wifi_devices:
        raise WifiError("No Wi-Fi adapter is available")
    return wifi_devices[0]


def _find_access_point(device, bssid: str):
    if not bssid:
        return None
    for access_point in device.get_access_points():
        if (access_point.get_bssid() or "").casefold() == bssid.casefold():
            return access_point
    return None


def _attach_timeout(GLib, context, milliseconds: int, callback):
    source = GLib.timeout_source_new(milliseconds)
    source.set_callback(callback)
    source.attach(context)
    return source


def _persist_active_profile(client, active, NM, GLib, context) -> None:
    # Modern libnm returns the RemoteConnection object itself here.  Older
    # introspection data exposed its D-Bus path instead, so retain that narrow
    # compatibility branch without ever passing an object to a string API.
    remote = active.get_connection()
    if isinstance(remote, str):
        remote = client.get_connection_by_path(remote)
    if remote is None:
        raise WifiError("NetworkManager did not publish the connected profile")
    loop = GLib.MainLoop.new(context, False)
    result: dict[str, object] = {}

    def completed(connection, async_result):
        try:
            connection.update2_finish(async_result)
        except Exception as error:
            result["error"] = error
        loop.quit()

    empty_arguments = GLib.Variant("a{sv}", {})
    remote.update2(
        # An empty settings dictionary means "persist the daemon's current
        # profile". This is essential for WPS: NetworkManager, not this
        # process, receives the provisioned PSK from the router.
        GLib.Variant("a{sa{sv}}", {}),
        NM.SettingsUpdate2Flags.TO_DISK,
        empty_arguments,
        None,
        completed,
    )
    loop.run()
    if "error" in result:
        raise WifiError(f"Connected, but could not save the Wi-Fi profile: {result['error']}")


def _saved_connection_with_credentials(remote, request, NM):
    """Clone a saved profile and replace only credentials supplied by the UI."""

    connection = NM.SimpleConnection.new_clone(remote)
    security = connection.get_setting_wireless_security()
    key_management = (security.get_key_mgmt() or "") if security else ""
    if key_management in ("wpa-psk", "sae"):
        security.props.psk = request.password
        security.props.wps_method = NM.SettingWirelessSecurityWpsMethod.DISABLED
    elif key_management == "none" and security is not None:
        security.props.wep_key0 = request.password
    elif key_management in ("wpa-eap", "wpa-eap-suite-b-192", "ieee8021x"):
        setting_8021x = connection.get_setting_802_1x()
        if setting_8021x is None:
            raise WifiError("The saved enterprise profile is incomplete")
        if request.identity:
            setting_8021x.props.identity = request.identity
        if request.eap_method is WifiEapMethod.TLS:
            if request.private_key:
                if not setting_8021x.set_private_key(
                    request.private_key,
                    request.private_key_password,
                    NM.Setting8021xCKScheme.PATH,
                    NM.Setting8021xCKFormat.UNKNOWN,
                ):
                    raise WifiError("The private key could not be loaded")
            elif request.private_key_password:
                setting_8021x.props.private_key_password = request.private_key_password
        else:
            setting_8021x.props.password = request.password
    try:
        connection.verify()
    except Exception as error:
        raise WifiError(f"The saved Wi-Fi profile is invalid: {error}") from error
    return connection


def connect_wifi(
    request: WifiConnectionRequest,
    *,
    wps: bool = False,
    profile_uuid: str | None = None,
    cancel_event: threading.Event | None = None,
    timeout: int = 60,
) -> None:
    """Create, activate, verify and persist a Wi-Fi profile entirely in-app."""

    validate_wifi_request(
        request,
        wps=wps,
        existing_profile=bool(profile_uuid and not wps),
    )
    Gio, GLib, _GObject, NM = _load_libnm()
    context = GLib.MainContext.new()
    context.push_thread_default()
    agent = None
    active = None
    try:
        client = NM.Client.new(None)
        device = _find_wifi_device(client, NM, request.device)
        access_point = _find_access_point(device, request.bssid)
        if request.bssid and access_point is None and not request.hidden:
            raise WifiError("The selected Wi-Fi access point is no longer available")
        remote = (
            client.get_connection_by_uuid(profile_uuid)
            if profile_uuid and not wps
            else None
        )
        if profile_uuid and remote is None and not wps:
            raise WifiError("The saved Wi-Fi profile is no longer available")
        if remote is not None:
            saved_wireless = remote.get_setting_wireless()
            saved_ssid = (
                NM.utils_ssid_to_utf8(saved_wireless.get_ssid().get_data())
                if saved_wireless and saved_wireless.get_ssid()
                else ""
            )
            if saved_ssid != request.ssid:
                raise WifiError("The saved Wi-Fi profile belongs to another network")
            connection = _saved_connection_with_credentials(remote, request, NM)
        else:
            connection = build_nm_connection(request, wps=wps)
        if wps:
            agent = _wps_agent_class(NM)()
            try:
                agent.register(None)
            except Exception as error:
                raise WifiError(f"Could not start the internal WPS agent: {error}") from error

        loop = GLib.MainLoop.new(context, False)
        outcome: dict[str, object] = {}
        started = time.monotonic()
        cancellable = Gio.Cancellable()

        def finish_with_error(error):
            if "error" not in outcome and "success" not in outcome:
                outcome["error"] = error
                loop.quit()

        def active_state_changed(active_connection, _property):
            state = active_connection.get_state()
            if state == NM.ActiveConnectionState.ACTIVATED:
                outcome["success"] = True
                loop.quit()
            elif state == NM.ActiveConnectionState.DEACTIVATED:
                reason = active_connection.get_state_reason()
                finish_with_error(WifiError(f"Wi-Fi activation failed: {reason.value_nick}"))

        def add_activation_started(source, async_result):
            nonlocal active
            try:
                active, _details = source.add_and_activate_connection2_finish(
                    async_result
                )
            except Exception as error:
                finish_with_error(WifiError(f"Could not start Wi-Fi activation: {error}"))
                return
            active.connect("notify::state", active_state_changed)
            active_state_changed(active, None)

        def saved_activation_started(source, async_result):
            nonlocal active
            try:
                active = source.activate_connection_finish(async_result)
            except Exception as error:
                finish_with_error(WifiError(f"Could not start Wi-Fi activation: {error}"))
                return
            active.connect("notify::state", active_state_changed)
            active_state_changed(active, None)

        options = GLib.Variant(
            "a{sv}", {"persist": GLib.Variant("s", "volatile")}
        )
        specific_object = access_point.get_path() if access_point is not None else None
        if remote is None:
            client.add_and_activate_connection2(
                connection,
                device,
                specific_object,
                options,
                cancellable,
                add_activation_started,
            )
        else:
            def saved_profile_updated(saved_connection, async_result):
                try:
                    saved_connection.update2_finish(async_result)
                except Exception as error:
                    finish_with_error(
                        WifiError(f"Could not update the saved Wi-Fi profile: {error}")
                    )
                    return
                client.activate_connection_async(
                    remote,
                    device,
                    specific_object,
                    cancellable,
                    saved_activation_started,
                )

            remote.update2(
                connection.to_dbus(NM.ConnectionSerializationFlags.ALL),
                NM.SettingsUpdate2Flags.TO_DISK,
                GLib.Variant("a{sv}", {}),
                cancellable,
                saved_profile_updated,
            )

        def poll_operation():
            if cancel_event is not None and cancel_event.is_set():
                cancellable.cancel()
                if active is not None:
                    client.deactivate_connection(active, None)
                finish_with_error(WifiCancelled("Wi-Fi connection was cancelled"))
                return False
            if time.monotonic() - started >= timeout:
                cancellable.cancel()
                if active is not None:
                    client.deactivate_connection(active, None)
                finish_with_error(WifiError("Wi-Fi connection timed out"))
                return False
            return True

        poll_source = _attach_timeout(GLib, context, 200, poll_operation)
        loop.run()
        poll_source.destroy()
        if "error" in outcome:
            raise outcome["error"]
        if remote is None:
            _persist_active_profile(client, active, NM, GLib, context)
    finally:
        if agent is not None:
            try:
                agent.unregister(None)
            except Exception:
                agent.destroy()
        context.pop_thread_default()


def current_wifi_profile_uuids() -> tuple[str, ...]:
    """Return active Wi-Fi UUIDs without exposing their stored secrets."""

    with _nm_client_context() as (client, NM, _context):
        return tuple(
            active.get_uuid()
            for active in client.get_active_connections()
            if active.get_connection_type() == NM.SETTING_WIRELESS_SETTING_NAME
            and active.get_uuid()
        )


def saved_wifi_profiles() -> tuple[WifiProfile, ...]:
    """List persistent Wi-Fi profiles without reading or returning secrets."""

    with _nm_client_context() as (client, NM, _context):
        return _saved_wifi_profiles_from_client(client, NM)


def _saved_wifi_profiles_from_client(client, NM) -> tuple[WifiProfile, ...]:
    """Copy libnm profile objects into context-independent value objects."""

    active_devices = {
        active.get_uuid(): next(
            (device.get_iface() for device in active.get_devices()), ""
        )
        for active in client.get_active_connections()
        if active.get_connection_type() == NM.SETTING_WIRELESS_SETTING_NAME
    }
    profiles = []
    for connection in client.get_connections():
        if connection.get_connection_type() != NM.SETTING_WIRELESS_SETTING_NAME:
            continue
        wireless = connection.get_setting_wireless()
        if wireless is None or wireless.get_ssid() is None:
            continue
        ssid = NM.utils_ssid_to_utf8(wireless.get_ssid().get_data())
        security = connection.get_setting_wireless_security()
        setting_8021x = connection.get_setting_802_1x()
        connection_uuid = connection.get_uuid()
        profiles.append(
            WifiProfile(
                uuid=connection_uuid,
                name=connection.get_id(),
                ssid=ssid,
                key_management=(security.get_key_mgmt() or "") if security else "",
                eap_method=(
                    setting_8021x.get_eap_method(0)
                    if setting_8021x and setting_8021x.get_num_eap_methods()
                    else ""
                ),
                active=connection_uuid in active_devices,
                device=active_devices.get(connection_uuid, ""),
            )
        )
    return tuple(
        sorted(profiles, key=lambda profile: (not profile.active, profile.name.casefold()))
    )
