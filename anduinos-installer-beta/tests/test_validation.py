import dataclasses
import unittest

from installer_core.model import (
    AuthenticationMode,
    Architecture,
    Firmware,
    MokPasswordPolicy,
    SecureBoot,
)
from installer_core.validation import PlanValidationError, validate_plan

from helpers import valid_plan


class ValidationTests(unittest.TestCase):
    def test_valid_amd64_uefi_secure_boot(self):
        validate_plan(valid_plan())

    def test_valid_amd64_bios(self):
        plan = valid_plan(
            architecture=Architecture.AMD64,
            firmware=Firmware.BIOS,
            secure_boot=SecureBoot.NOT_APPLICABLE,
        )
        validate_plan(plan)

    def test_valid_arm64_uefi_secure_boot(self):
        plan = valid_plan(
            architecture=Architecture.ARM64,
            firmware=Firmware.UEFI,
            secure_boot=SecureBoot.ENABLED,
        )
        validate_plan(plan)

    def test_rejects_arm64_bios(self):
        plan = valid_plan(
            architecture=Architecture.ARM64,
            firmware=Firmware.BIOS,
            secure_boot=SecureBoot.NOT_APPLICABLE,
        )
        with self.assertRaisesRegex(
            PlanValidationError, "arm64 supports standards-based UEFI only"
        ):
            validate_plan(plan)

    def test_rejects_partition_instead_of_whole_disk(self):
        plan = valid_plan()
        bad_disk = dataclasses.replace(plan.storage.disk, path="/dev/sda1")
        plan = dataclasses.replace(
            plan, storage=dataclasses.replace(plan.storage, disk=bad_disk)
        )
        with self.assertRaisesRegex(PlanValidationError, "whole-disk"):
            validate_plan(plan)

    def test_rejects_wrong_mok_policy(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            boot=dataclasses.replace(
                plan.boot,
                mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE,
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "MOK password"):
            validate_plan(plan)

    def test_rejects_non_boolean_software_choice(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            software=dataclasses.replace(
                plan.software, install_updates="yes"
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "boolean"):
            validate_plan(plan)

    def test_rejects_password_in_passwordless_plan(self):
        plan = valid_plan(authentication=AuthenticationMode.PASSWORDLESS_SHARED)
        plan = dataclasses.replace(
            plan,
            identity=dataclasses.replace(
                plan.identity, password_hash="$6$unexpected"
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "must not carry"):
            validate_plan(plan)

    def test_passwordless_shared_plan_requires_nopasswd_sudo(self):
        plan = valid_plan(authentication=AuthenticationMode.PASSWORDLESS_SHARED)
        plan = dataclasses.replace(
            plan,
            identity=dataclasses.replace(
                plan.identity, sudo_without_password=False
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "requires"):
            validate_plan(plan)


if __name__ == "__main__":
    unittest.main()
