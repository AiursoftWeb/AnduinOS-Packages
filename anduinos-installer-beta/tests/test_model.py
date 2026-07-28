import unittest

from installer_core.model import InstallPlan

from helpers import valid_plan


class InstallPlanTests(unittest.TestCase):
    def test_round_trip(self):
        plan = valid_plan()
        restored = InstallPlan.from_dict(plan.to_dict())
        self.assertEqual(restored, plan)

    def test_repr_does_not_expose_password_hash(self):
        plan = valid_plan()
        self.assertNotIn(plan.identity.password_hash, repr(plan.identity))


if __name__ == "__main__":
    unittest.main()
