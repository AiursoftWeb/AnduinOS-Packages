import unittest

from helpers import valid_plan
from installer_core.model import (
    Architecture,
    DiskIdentity,
    Firmware,
    SecureBoot,
)
from installer_core.planning import build_plan
from installer_core.probe import PlatformProbe


class PlanningTests(unittest.TestCase):
    def test_chinese_plan_selects_rime_and_mok_policy(self):
        original = valid_plan()
        choices = {
            "lang": "zh_CN",
            "locale": "zh_CN.UTF-8",
            "keyboard": "cn",
            "filesystem": "btrfs",
            "hostname": original.identity.hostname,
            "username": original.identity.username,
            "full_name": original.identity.full_name,
            "timezone": "Asia/Shanghai",
        }
        disk = DiskIdentity("/dev/sda", "serial:test", 64 * 1024**3)
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        plan = build_plan(choices, disk, platform, "$y$j9T$example$example")
        self.assertEqual(plan.regional.input_method, "rime")
        self.assertEqual(plan.boot.mok_password_policy.value, "anduinos-default")

