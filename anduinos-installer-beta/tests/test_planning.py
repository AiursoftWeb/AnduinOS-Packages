import unittest

from helpers import (
    TEST_INVENTORY_DIGEST,
    TEST_TOPOLOGY_DIGEST,
    valid_plan,
)
from installer_core.model import (
    Architecture,
    DiskIdentity,
    Firmware,
    SecureBoot,
)
from installer_core.planning import build_plan
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import DiskTopologyBinding
from installer_core.swap_policy import GIB


class PlanningTests(unittest.TestCase):
    def test_chinese_plan_selects_rime_and_mok_policy(self):
        original = valid_plan()
        choices = {
            "lang": "zh_CN",
            "locale": "zh_CN.UTF-8",
            "keyboard": "us",
            "filesystem": "btrfs",
            "hostname": original.identity.hostname,
            "username": original.identity.username,
            "full_name": original.identity.full_name,
            "timezone": "Asia/Shanghai",
            "install_updates": False,
            "install_third_party_drivers": True,
            "install_multimedia_codecs": True,
            "sudo_without_password": True,
        }
        disk = DiskIdentity("/dev/sda", "serial:test", 64 * 1024**3)
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        plan = build_plan(
            choices,
            disk,
            platform,
            "$y$j9T$example$example",
            disk_binding=DiskTopologyBinding(
                disk.stable_id,
                disk.expected_size_bytes,
                TEST_TOPOLOGY_DIGEST,
            ),
            inventory_digest=TEST_INVENTORY_DIGEST,
            physical_memory_probe=lambda: 8 * GIB,
        )
        self.assertEqual(plan.regional.input_methods, ("rime",))
        self.assertEqual(plan.regional.keyboard.layout, "us")
        self.assertEqual(plan.boot.mok_password_policy.value, "anduinos-default")
        self.assertFalse(plan.software.install_updates)
        self.assertTrue(plan.software.install_third_party_drivers)
        self.assertTrue(plan.software.install_multimedia_codecs)
        self.assertTrue(plan.identity.sudo_without_password)
        self.assertIsNotNone(plan.storage.graph)
        self.assertEqual(plan.storage.swap_size_mib, 9 * 1024)

    def test_locale_not_untrusted_ui_language_field_selects_input_method(self):
        original = valid_plan()
        choices = {
            "lang": "en_US",
            "locale": "zh_CN.UTF-8",
            "keyboard": "us",
            "hostname": original.identity.hostname,
            "username": original.identity.username,
            "full_name": original.identity.full_name,
            "timezone": "Asia/Shanghai",
        }
        plan = build_plan(
            choices,
            DiskIdentity("/dev/sda", "serial:test", 64 * 1024**3),
            PlatformProbe(
                Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED
            ),
            "$y$j9T$example$example",
            disk_binding=DiskTopologyBinding(
                "serial:test", 64 * 1024**3, TEST_TOPOLOGY_DIGEST
            ),
            inventory_digest=TEST_INVENTORY_DIGEST,
        )
        self.assertEqual(plan.regional.input_methods, ("rime",))

    def test_recommended_input_method_can_be_declined(self):
        original = valid_plan()
        choices = {
            "locale": "zh_CN.UTF-8",
            "keyboard": "us",
            "hostname": original.identity.hostname,
            "username": original.identity.username,
            "full_name": original.identity.full_name,
            "timezone": "Asia/Shanghai",
            "input_methods": (),
        }
        plan = build_plan(
            choices,
            DiskIdentity("/dev/sda", "serial:test", 64 * 1024**3),
            PlatformProbe(
                Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED
            ),
            "$y$j9T$example$example",
            disk_binding=DiskTopologyBinding(
                "serial:test", 64 * 1024**3, TEST_TOPOLOGY_DIGEST
            ),
            inventory_digest=TEST_INVENTORY_DIGEST,
        )
        self.assertEqual(plan.regional.input_methods, ())

    def test_multiple_recommended_input_methods_can_be_selected(self):
        original = valid_plan()
        choices = {
            "locale": "zh_CN.UTF-8",
            "keyboard": "us",
            "hostname": original.identity.hostname,
            "username": original.identity.username,
            "full_name": original.identity.full_name,
            "timezone": "Asia/Shanghai",
            "input_methods": ("rime", "wubi"),
        }
        plan = build_plan(
            choices,
            DiskIdentity("/dev/sda", "serial:test", 64 * 1024**3),
            PlatformProbe(
                Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED
            ),
            "$y$j9T$example$example",
            disk_binding=DiskTopologyBinding(
                "serial:test", 64 * 1024**3, TEST_TOPOLOGY_DIGEST
            ),
            inventory_digest=TEST_INVENTORY_DIGEST,
        )
        self.assertEqual(plan.regional.input_methods, ("rime", "wubi"))
