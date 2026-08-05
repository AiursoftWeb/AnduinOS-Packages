import unittest
from dataclasses import replace

from installer_core.model import InstallPlan

from helpers import valid_plan


class InstallPlanTests(unittest.TestCase):
    def test_round_trip(self):
        plan = valid_plan()
        plan = replace(
            plan,
            regional=replace(
                plan.regional,
                locale="zh_CN.UTF-8",
                timezone="Asia/Shanghai",
                input_methods=("rime", "wubi"),
            ),
        )
        restored = InstallPlan.from_dict(plan.to_dict())
        self.assertEqual(restored, plan)

    def test_rejects_non_list_input_methods_at_privilege_boundary(self):
        value = valid_plan().to_dict()
        value["regional"]["input_methods"] = "rime"
        with self.assertRaisesRegex(TypeError, "must be a list of strings"):
            InstallPlan.from_dict(value)

    def test_repr_does_not_expose_password_hash(self):
        plan = valid_plan()
        self.assertNotIn(plan.identity.password_hash, repr(plan.identity))

    def test_rejects_unknown_top_level_field(self):
        value = valid_plan().to_dict()
        value["future_command"] = "mkfs.anything"
        with self.assertRaisesRegex(ValueError, "Unknown field in plan"):
            InstallPlan.from_dict(value)

    def test_rejects_unknown_nested_field(self):
        value = valid_plan().to_dict()
        value["storage"]["disk"]["authoritative_path"] = "/dev/attacker"
        with self.assertRaisesRegex(
            ValueError, "Unknown field in storage.disk"
        ):
            InstallPlan.from_dict(value)


if __name__ == "__main__":
    unittest.main()
