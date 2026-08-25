import dataclasses
import unittest

from installer_core.model import (
    AuthenticationMode,
    Architecture,
    Firmware,
    MokPasswordPolicy,
    SecureBoot,
)
from installer_core.validation import (
    PlanValidationError,
    validate_plan,
    validate_plan_for_execution,
)

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

    def test_valid_uefi_without_secure_boot_support(self):
        validate_plan(valid_plan(secure_boot=SecureBoot.UNSUPPORTED))

    def test_accepts_a_catalogued_keyboard_variant(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            regional=dataclasses.replace(
                plan.regional,
                keyboard=dataclasses.replace(
                    plan.regional.keyboard,
                    layout="us",
                    variant="intl",
                ),
            ),
        )
        validate_plan(plan)

    def test_rejects_unknown_or_unsafe_keyboard_variants(self):
        for variant, message in (
            ("qwerty", "Unknown keyboard layout and variant combination"),
            ('intl"\nXKBOPTIONS="terminate:ctrl_alt_bksp', "Invalid keyboard variant"),
        ):
            with self.subTest(variant=variant):
                plan = valid_plan()
                plan = dataclasses.replace(
                    plan,
                    regional=dataclasses.replace(
                        plan.regional,
                        keyboard=dataclasses.replace(
                            plan.regional.keyboard,
                            variant=variant,
                        ),
                    ),
                )
                with self.assertRaisesRegex(PlanValidationError, message):
                    validate_plan(plan)

    def test_rejects_noncanonical_hostname_from_an_untrusted_plan(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            identity=dataclasses.replace(
                plan.identity, hostname="TT-VIEW-71"
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "Invalid hostname"):
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

        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            software=dataclasses.replace(
                plan.software, install_multimedia_codecs="yes"
            ),
        )
        with self.assertRaisesRegex(
            PlanValidationError, "Multimedia-codec policy must be boolean"
        ):
            validate_plan(plan)

    def test_accepts_multiple_input_methods_for_the_locale(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            regional=dataclasses.replace(
                plan.regional,
                locale="zh_CN.UTF-8",
                timezone="Asia/Shanghai",
                input_methods=("rime", "wubi"),
            ),
        )
        validate_plan(plan)

    def test_rejects_an_input_method_recommended_for_another_locale(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            regional=dataclasses.replace(
                plan.regional,
                locale="zh_CN.UTF-8",
                timezone="Asia/Shanghai",
                input_methods=("cangjie",),
            ),
        )
        with self.assertRaisesRegex(
            PlanValidationError,
            "do not match installer language policy",
        ):
            validate_plan(plan)

    def test_rejects_duplicate_input_methods(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            regional=dataclasses.replace(
                plan.regional,
                locale="zh_CN.UTF-8",
                timezone="Asia/Shanghai",
                input_methods=("rime", "rime"),
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "duplicates"):
            validate_plan(plan)

    def test_rejects_input_methods_outside_policy_order(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            regional=dataclasses.replace(
                plan.regional,
                locale="zh_CN.UTF-8",
                timezone="Asia/Shanghai",
                input_methods=("wubi", "rime"),
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "policy order"):
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

    def test_release_execution_rejects_passwordless_erase_installation(self):
        plan = valid_plan(authentication=AuthenticationMode.PASSWORDLESS_SHARED)
        with self.assertRaisesRegex(
            PlanValidationError, "password-protected account"
        ):
            validate_plan_for_execution(plan)

    def test_passwordless_shared_plan_requires_nopasswd_sudo(self):
        plan = valid_plan(authentication=AuthenticationMode.PASSWORDLESS_SHARED)
        plan = dataclasses.replace(
            plan,
            access=dataclasses.replace(
                plan.access, sudo_without_password=False
            ),
        )
        with self.assertRaisesRegex(PlanValidationError, "requires"):
            validate_plan(plan)


    def test_rejects_non_ascii_or_overlong_username(self):
        for username in ("Alice", "alice-smith", "张三", "a" * 17):
            with self.subTest(username=username):
                plan = valid_plan()
                plan = dataclasses.replace(
                    plan,
                    identity=dataclasses.replace(
                        plan.identity, username=username
                    ),
                )
                with self.assertRaisesRegex(
                    PlanValidationError, "Invalid username"
                ):
                    validate_plan(plan)

    def test_accepts_long_full_name_with_truncated_username(self):
        plan = valid_plan()
        plan = dataclasses.replace(
            plan,
            identity=dataclasses.replace(
                plan.identity,
                full_name="Alexandria Johnson",
                username="alexandriajohnso",
            ),
        )
        validate_plan(plan)

    def test_rejects_reserved_usernames(self):
        for username in ("root", "live", "ubuntu"):
            with self.subTest(username=username):
                plan = valid_plan()
                plan = dataclasses.replace(
                    plan,
                    identity=dataclasses.replace(
                        plan.identity, username=username
                    ),
                )
                with self.assertRaisesRegex(
                    PlanValidationError, "Reserved username"
                ):
                    validate_plan(plan)


if __name__ == "__main__":
    unittest.main()
