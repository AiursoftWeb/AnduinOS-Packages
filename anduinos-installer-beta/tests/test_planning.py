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
        )
        self.assertEqual(plan.regional.input_method, "rime")
        self.assertEqual(plan.regional.keyboard.layout, "us")
        self.assertEqual(plan.boot.mok_password_policy.value, "anduinos-default")
        self.assertFalse(plan.software.install_updates)
        self.assertTrue(plan.software.install_third_party_drivers)
        self.assertTrue(plan.identity.sudo_without_password)
        self.assertIsNotNone(plan.storage.graph)
