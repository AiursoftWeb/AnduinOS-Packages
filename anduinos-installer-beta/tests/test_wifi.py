import unittest
from pathlib import Path

try:
    import gi

    gi.require_version("NM", "1.0")
    from gi.repository import GLib, NM
except (ImportError, ValueError):
    GLib = None
    NM = None

from installer_core.wifi import (
    WifiConnectionRequest,
    WifiEapMethod,
    WifiError,
    WifiSecurity,
    _persist_active_profile,
    build_nm_connection,
    classify_wifi_security,
    disconnect_wifi,
    parse_wifi_networks,
    scan_wifi_networks,
    set_wifi_radio,
    split_nmcli_terse,
    wifi_radio_enabled,
    validate_wifi_request,
)


requires_libnm = unittest.skipUnless(
    NM is not None,
    "NetworkManager introspection bindings are not installed",
)


class FakeSsid:
    def __init__(self, value):
        self.value = value.encode("utf-8")

    def get_data(self):
        return self.value


class FakeAccessPoint:
    def __init__(
        self,
        ssid,
        path,
        *,
        strength=50,
        bssid="00:11:22:33:44:55",
        flags=0,
        wpa=0,
        rsn=0,
    ):
        self.ssid = FakeSsid(ssid)
        self.path = path
        self.strength = strength
        self.bssid = bssid
        self.flags = flags
        self.wpa = wpa
        self.rsn = rsn

    def get_ssid(self):
        return self.ssid

    def get_path(self):
        return self.path

    def get_strength(self):
        return self.strength

    def get_bssid(self):
        return self.bssid

    def get_flags(self):
        return self.flags

    def get_wpa_flags(self):
        return self.wpa

    def get_rsn_flags(self):
        return self.rsn


class FakeWifiDevice:
    def __init__(self, interface, access_points=(), active=None):
        self.interface = interface
        self.access_points = list(access_points)
        self.active = active
        self.last_scan = 10
        self.scan_requests = 0
        self.disconnected = False

    def get_device_type(self):
        return NM.DeviceType.WIFI

    def get_iface(self):
        return self.interface

    def get_path(self):
        return f"/device/{self.interface}"

    def get_access_points(self):
        return self.access_points

    def get_active_access_point(self):
        return self.active

    def get_last_scan(self):
        return self.last_scan

    def request_scan(self, _cancellable):
        self.scan_requests += 1
        self.last_scan += 1
        return True

    def disconnect(self, _cancellable):
        self.disconnected = True
        return True


class FakeClient:
    def __init__(self, devices=(), radio=True):
        self.devices = list(devices)
        self.radio = radio
        self.radio_changes = []

    def get_devices(self):
        return self.devices

    def wireless_get_enabled(self):
        return self.radio

    def wireless_set_enabled(self, enabled):
        self.radio = enabled
        self.radio_changes.append(enabled)


class WifiDiscoveryTests(unittest.TestCase):
    @requires_libnm
    def test_persist_uses_the_remote_connection_returned_by_libnm(self):
        context = GLib.MainContext.new()

        class RemoteConnection:
            def __init__(self):
                self.updated = False

            def update2(
                self, settings, flags, arguments, _cancellable, callback
            ):
                self.updated = True
                self.settings = settings.unpack()
                self.flags = flags
                self.arguments = arguments.unpack()
                source = GLib.idle_source_new()

                def complete():
                    callback(self, object())
                    return GLib.SOURCE_REMOVE

                source.set_callback(complete)
                source.attach(context)

            def update2_finish(self, _result):
                return True

        class ActiveConnection:
            def __init__(self, remote):
                self.remote = remote

            def get_connection(self):
                return self.remote

        class Client:
            def get_connection_by_path(self, _path):
                raise AssertionError(
                    "RemoteConnection must not be passed to a path API"
                )

        remote = RemoteConnection()
        _persist_active_profile(
            Client(), ActiveConnection(remote), NM, GLib, context
        )

        self.assertTrue(remote.updated)
        self.assertEqual(remote.settings, {})
        self.assertEqual(remote.arguments, {})
        self.assertEqual(remote.flags, NM.SettingsUpdate2Flags.TO_DISK)

    def test_frontend_wifi_backend_never_spawns_an_external_process(self):
        source = (
            Path(__file__).parents[1] / "src/installer_core/wifi.py"
        ).read_text()
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("subprocess.", source)

    def test_split_preserves_escaped_colons_and_backslashes(self):
        self.assertEqual(
            split_nmcli_terse(r"*:Cafe\: Guest:91:WPA2\\WPA3"),
            ("*", "Cafe: Guest", "91", "WPA2\\WPA3"),
        )

    def test_scan_results_are_deduplicated_and_ranked(self):
        networks = parse_wifi_networks(
            "*:Home:62:WPA2\n"
            ":Cafe\\: Guest:88:--\n"
            ":Home:95:WPA2\n"
            ":Hidden:invalid:WPA2\n"
            ":This record is incomplete\n"
        )
        self.assertEqual(
            [(item.ssid, item.signal, item.security, item.active)
             for item in networks],
            [
                ("Home", 62, "WPA2", True),
                ("Cafe: Guest", 88, "--", False),
                ("Hidden", 0, "WPA2", False),
            ],
        )

    def test_extended_scan_tracks_adapter_bssid_security_and_wps(self):
        networks = parse_wifi_networks(
            ":Cafe:00\\:11\\:22\\:33\\:44\\:55:84:--:wlan0:(none):(none)\n"
            "*:Corp:AA\\:BB\\:CC\\:DD\\:EE\\:FF:58:WPA2 802.1X:wlan1:"
            "(none):pair_ccmp group_ccmp 802_1x\n",
            {("wlan1", "AA:BB:CC:DD:EE:FF"): (True, True)},
        )
        self.assertEqual(networks[0].ssid, "Corp")
        self.assertEqual(networks[0].security_kind, WifiSecurity.ENTERPRISE)
        self.assertEqual(networks[0].device, "wlan1")
        self.assertTrue(networks[0].wps_pbc)
        self.assertTrue(networks[0].wps_pin)
        self.assertEqual(networks[1].security_kind, WifiSecurity.OPEN)

    def test_security_classifier_covers_modern_and_legacy_networks(self):
        self.assertEqual(classify_wifi_security("--"), WifiSecurity.OPEN)
        self.assertEqual(classify_wifi_security("OWE"), WifiSecurity.OWE)
        self.assertEqual(classify_wifi_security("WEP"), WifiSecurity.WEP)
        self.assertEqual(classify_wifi_security("WPA2 WPA3"), WifiSecurity.PERSONAL)
        self.assertEqual(
            classify_wifi_security("WPA2 802.1X"), WifiSecurity.ENTERPRISE
        )
    @requires_libnm
    def test_scan_uses_libnm_cache_and_requests_a_fresh_scan(self):
        security_flags = getattr(NM, "80211ApSecurityFlags")
        ap_flags = getattr(NM, "80211ApFlags")
        cafe = FakeAccessPoint("Cafe", "/ap/cafe", strength=87)
        home = FakeAccessPoint(
            "Home",
            "/ap/home",
            strength=62,
            bssid="AA:BB:CC:DD:EE:FF",
            flags=int(ap_flags.WPS) | int(ap_flags.WPS_PBC),
            rsn=int(security_flags.KEY_MGMT_PSK),
        )
        device = FakeWifiDevice("wlan0", (cafe, home), active=home)

        networks = scan_wifi_networks(_client=FakeClient((device,)))

        self.assertEqual(device.scan_requests, 1)
        self.assertEqual([item.ssid for item in networks], ["Home", "Cafe"])
        self.assertTrue(networks[0].active)
        self.assertTrue(networks[0].wps_pbc)
        self.assertEqual(networks[0].security_kind, WifiSecurity.PERSONAL)
        self.assertEqual(networks[1].security_kind, WifiSecurity.OPEN)

    @requires_libnm
    def test_background_refresh_reuses_networkmanagers_scan_cache(self):
        device = FakeWifiDevice("wlan0")
        self.assertEqual(
            scan_wifi_networks(rescan=False, _client=FakeClient((device,))), ()
        )
        self.assertEqual(device.scan_requests, 0)

    @requires_libnm
    def test_radio_state_comes_directly_from_libnm(self):
        self.assertTrue(wifi_radio_enabled(_client=FakeClient(radio=True)))
        self.assertFalse(wifi_radio_enabled(_client=FakeClient(radio=False)))

    @requires_libnm
    def test_radio_toggle_uses_libnm_without_spawning_a_process(self):
        client = FakeClient(radio=False)
        set_wifi_radio(True, _client=client)
        set_wifi_radio(False, _client=client)
        self.assertEqual(client.radio_changes, [True, False])

    @requires_libnm
    def test_disconnect_targets_only_the_selected_adapter(self):
        wlan0 = FakeWifiDevice("wlan0")
        wlan7 = FakeWifiDevice("wlan7")
        client = FakeClient((wlan0, wlan7))
        disconnect_wifi("wlan7", _client=client)
        self.assertFalse(wlan0.disconnected)
        self.assertTrue(wlan7.disconnected)
        with self.assertRaises(WifiError):
            disconnect_wifi("bad device", _client=client)

    @requires_libnm
    def test_personal_profiles_keep_secrets_in_memory(self):
        connection = build_nm_connection(
            WifiConnectionRequest(
                ssid="Home",
                security=WifiSecurity.PERSONAL,
                security_label="WPA2 WPA3",
                password="correct horse battery staple",
            )
        )
        security = connection.get_setting_wireless_security()
        self.assertEqual(security.get_key_mgmt(), "wpa-psk")
        self.assertEqual(security.get_psk(), "correct horse battery staple")
        self.assertTrue(connection.verify())

        wpa3 = build_nm_connection(
            WifiConnectionRequest(
                ssid="WPA3 only",
                security=WifiSecurity.PERSONAL,
                security_label="WPA3",
                password="modern-password",
            )
        )
        self.assertEqual(
            wpa3.get_setting_wireless_security().get_key_mgmt(), "sae"
        )

    @requires_libnm
    def test_wps_profile_has_no_password_and_requests_push_button(self):
        connection = build_nm_connection(
            WifiConnectionRequest("WPS network", WifiSecurity.PERSONAL),
            wps=True,
        )
        security = connection.get_setting_wireless_security()
        self.assertIsNone(security.get_psk())
        self.assertNotEqual(int(security.get_wps_method()), 0)
        self.assertTrue(connection.verify())

    @requires_libnm
    def test_open_owe_wep_and_enterprise_profiles_verify(self):
        open_connection = build_nm_connection(
            WifiConnectionRequest("Cafe", WifiSecurity.OPEN)
        )
        self.assertIsNone(open_connection.get_setting_wireless_security())

        owe = build_nm_connection(
            WifiConnectionRequest("Encrypted open", WifiSecurity.OWE)
        )
        self.assertEqual(owe.get_setting_wireless_security().get_key_mgmt(), "owe")

        wep = build_nm_connection(
            WifiConnectionRequest("Legacy", WifiSecurity.WEP, password="abcde")
        )
        self.assertEqual(wep.get_setting_wireless_security().get_wep_key(0), "abcde")

        enterprise = build_nm_connection(
            WifiConnectionRequest(
                "Company",
                WifiSecurity.ENTERPRISE,
                identity="employee@example.com",
                anonymous_identity="anonymous@example.com",
                password="enterprise-secret",
                eap_method=WifiEapMethod.TTLS,
                phase2_auth="pap",
                domain_suffix_match="example.com",
            )
        )
        setting = enterprise.get_setting_802_1x()
        self.assertEqual(setting.get_eap_method(0), "ttls")
        self.assertEqual(setting.get_phase2_auth(), "pap")
        self.assertEqual(setting.get_password(), "enterprise-secret")
        self.assertTrue(enterprise.verify())

    def test_invalid_credentials_are_rejected_before_activation(self):
        for request, message in (
            (
                WifiConnectionRequest("Home", WifiSecurity.PERSONAL, password="short"),
                "8 to 63",
            ),
            (
                WifiConnectionRequest("Corp", WifiSecurity.ENTERPRISE, password="secret"),
                "identity",
            ),
            (
                WifiConnectionRequest(
                    "TLS",
                    WifiSecurity.ENTERPRISE,
                    identity="person",
                    eap_method=WifiEapMethod.TLS,
                ),
                "client certificate",
            ),
        ):
            with self.subTest(request=request):
                with self.assertRaisesRegex(WifiError, message):
                    build_nm_connection(request)

    def test_saved_enterprise_tls_can_reuse_its_certificate_settings(self):
        validate_wifi_request(
            WifiConnectionRequest(
                "Saved TLS",
                WifiSecurity.ENTERPRISE,
                eap_method=WifiEapMethod.TLS,
                private_key_password="token-password",
            ),
            existing_profile=True,
        )


if __name__ == "__main__":
    unittest.main()
