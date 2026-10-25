"""Business contract for the proposed pure Secure Boot page calculator.

The private API reuses SecureBootState and DkmsState:

    Facts(secure_boot, dkms, *, drivers_checked, next_boot=None)
    NextBootEvidence(loader_source, loader_path, kernel_release,
                     firmware_trusted, loader_trusted, kernel_trusted, modules_trusted)
    decide(facts) -> page.rows and page.next_row
    assess_action(action_id, facts) -> the same action decision used by the UI

Rows expose id/tone/reason/action. Actions expose id/allowed/blockers; each
blocker exposes reason/row_id. Reasons and IDs are stable English codes, not
translated labels. No changes to the public status CLI are implied.
"""
from copy import deepcopy
from dataclasses import replace
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_secureboot.identity import IdentityStatus
from anduinos_secureboot.model import (
    DkmsState, GrubState, ModuleState, SecureBootState, SecureBootStatus,
)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.core = importlib.import_module("anduinos_secureboot.decision")
        except ModuleNotFoundError as error:
            if error.name != "anduinos_secureboot.decision":
                raise
            self.fail("RED: the pure decision module has not been implemented")
        self.identity = IdentityStatus(
            certificate="/fixture/new/MOK.der", fingerprint="new-certificate",
            purpose="unified", enrollment="enrolled", managed=True,
            key_present=True, key_valid=True, defaults_ready=True,
        )
        self.boot = GrubState(installed=True, signature="valid", enrollment="enrolled",
                              deployment="ready", active_chain="trusted")
        self.state = SecureBootState(
            enabled=True, key_present=True, certificate_present=True, enrolled=True,
            certificate_serial="new-signer", configuration_present=True,
            status=SecureBootStatus.ENABLED, setup_mode=False, boot_loader="shim",
            dkms_available=True, headers_available=True,
            enrolled_certificate_serials=("old-signer", "new-signer"),
            signing_identity=self.identity, grub=self.boot,
        )
        self.drivers = DkmsState(
            modules=("example.ko",), trusted_modules=("example.ko",),
            details=(ModuleState("example.ko", "/fixture/example.ko", "old-signer", True, False),),
        )

    def migration_state(self, identity):
        """A new target identity coexists with the still-selected old trust."""
        return replace(self.state, certificate_serial="old-signer", enrolled=True,
                       enrolled_certificate_serials=("old-signer",),
                       signing_identity=identity, grub=GrubState())

    def evidence(self, **changes):
        values = dict(loader_source="anduinos", loader_path=r"\EFI\AnduinOS\shimx64.efi",
                      kernel_release="fixture-next-kernel", firmware_trusted=True,
                      loader_trusted=True, kernel_trusted=True, modules_trusted=True)
        values.update(changes)
        return self.core.NextBootEvidence(**values)

    def facts(self, *, state=None, drivers=None, drivers_checked=True, next_boot=None):
        return self.core.Facts(
            secure_boot=self.state if state is None else state,
            dkms=self.drivers if drivers is None else drivers,
            drivers_checked=drivers_checked, next_boot=next_boot,
        )

    def row(self, facts, row_id):
        rows = [row for row in self.core.decide(facts).rows if row.id == row_id]
        self.assertEqual(len(rows), 1, f"Expected exactly one {row_id} row")
        return rows[0]

    def assert_action(self, row, action_id, *, allowed):
        self.assertIsNotNone(row.action, "An unresolved row must offer a next step")
        self.assertEqual(row.action.id, action_id)
        self.assertEqual(row.action.allowed, allowed)
        if allowed:
            self.assertEqual(tuple(row.action.blockers), ())
        else:
            self.assertTrue(row.action.blockers, "Blocked actions must explain why")
        return row.action

    def assert_blocker(self, action, reason, row_id):
        self.assertIn((reason, row_id), [(b.reason, b.row_id) for b in action.blockers])

    def test_healthy_installation_has_six_success_rows_and_no_repair(self):
        page = self.core.decide(self.facts(next_boot=self.evidence()))
        self.assertEqual([row.id for row in page.rows], [
            "secure_boot", "certificate", "certificate_purpose", "enrollment", "drivers", "bootloader",
        ])
        self.assertEqual([row.tone for row in page.rows], ["success"] * 6)
        self.assertTrue(all(row.action is None for row in page.rows))
        self.assertIsNone(page.next_row)

    def test_missing_identity_offers_creation_with_secure_boot_on_or_off(self):
        identity = IdentityStatus(purpose="missing", enrollment="absent")
        for status in (SecureBootStatus.ENABLED, SecureBootStatus.DISABLED):
            with self.subTest(status=status):
                state = replace(self.state, status=status, enabled=status is SecureBootStatus.ENABLED,
                                key_present=False, certificate_present=False, enrolled=False,
                                signing_identity=identity, grub=GrubState())
                facts = self.facts(state=state)
                row = self.row(facts, "certificate")
                self.assertEqual(row.tone, "warning")
                self.assert_action(row, "prepare-identity", allowed=True)
                self.assertEqual(self.core.decide(facts).next_row, "certificate")

    def test_module_only_certificate_can_keep_working_drivers_but_blocks_grub(self):
        identity = replace(self.identity, purpose="module-only", managed=False, defaults_ready=None)
        facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()))
        self.assertEqual(self.row(facts, "drivers").tone, "success")
        purpose = self.row(facts, "certificate_purpose")
        self.assertEqual(purpose.tone, "warning")
        self.assert_action(purpose, "prepare-identity", allowed=True)
        action = self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)
        self.assert_blocker(action, "unified-certificate-required", "certificate_purpose")

    def test_wrong_or_missing_private_key_never_allows_bootloader_installation(self):
        for present, valid in ((True, False), (False, False), (True, None)):
            with self.subTest(present=present, valid=valid):
                identity = replace(self.identity, key_present=present, key_valid=valid)
                facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()))
                action = self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)
                self.assert_blocker(action, "signing-key-unavailable", "certificate")
                self.assertIsNotNone(self.row(facts, "certificate").action)

    def test_expired_identity_requires_replacement_even_when_enrolled(self):
        identity = replace(self.identity, purpose="expired")
        facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()))
        self.assert_action(self.row(facts, "certificate_purpose"), "regenerate-identity", allowed=True)
        self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)

    def test_identity_inspection_failure_offers_recheck_not_creation(self):
        identity = IdentityStatus(detail="Inspection was not authorized")
        facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()))
        row = self.row(facts, "certificate")
        self.assertEqual(row.tone, "warning")
        self.assert_action(row, "refresh", allowed=True)
        self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)

    def test_unenrolled_valid_identity_offers_submission_not_regeneration(self):
        identity = replace(self.identity, enrollment="absent", defaults_ready=False)
        facts = self.facts(state=self.migration_state(identity))
        self.assert_action(self.row(facts, "enrollment"), "prepare-identity", allowed=True)
        self.assertEqual(self.core.decide(facts).next_row, "enrollment")

    def test_pending_identity_offers_reboot_and_explains_bootloader_blocker(self):
        identity = replace(self.identity, enrollment="pending", defaults_ready=False)
        facts = self.facts(state=self.migration_state(identity),
                           next_boot=self.evidence(loader_source="ubuntu"))
        self.assertEqual(self.row(facts, "enrollment").tone, "warning")
        self.assert_action(self.row(facts, "enrollment"), "reboot-enroll", allowed=True)
        action = self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)
        self.assert_blocker(action, "certificate-enrollment-required", "enrollment")
        self.assertEqual(self.core.decide(facts).next_row, "enrollment")

    def test_skipping_enrollment_allows_resubmitting_the_same_identity(self):
        pending = replace(self.identity, enrollment="pending", defaults_ready=False)
        before = self.facts(state=self.migration_state(pending),
                            next_boot=self.evidence(loader_source="ubuntu"))
        after = self.facts(state=replace(before.secure_boot,
                           signing_identity=replace(pending, enrollment="absent")), next_boot=before.next_boot)
        self.assert_action(self.row(before, "enrollment"), "reboot-enroll", allowed=True)
        self.assert_action(self.row(after, "enrollment"), "prepare-identity", allowed=True)
        self.assertEqual(after.secure_boot.signing_identity.fingerprint, pending.fingerprint)

    def test_old_enrollment_does_not_make_new_identity_enrolled(self):
        identity = replace(self.identity, enrollment="absent", defaults_ready=False)
        facts = self.facts(state=self.migration_state(identity))
        self.assertEqual(self.row(facts, "enrollment").tone, "warning")
        self.assert_action(self.row(facts, "enrollment"), "prepare-identity", allowed=True)

    def test_unknown_enrollment_offers_recheck_not_recreate_or_restart(self):
        facts = self.facts(state=self.migration_state(replace(self.identity, enrollment="unknown")))
        self.assert_action(self.row(facts, "enrollment"), "refresh", allowed=True)

    def test_enrollment_does_not_turn_a_green_certificate_into_a_new_warning(self):
        drivers = DkmsState(modules=("driver.ko",), untrusted_modules=("driver.ko",))
        for ready in (False, None):
            for enrollment in ("absent", "pending"):
                with self.subTest(defaults_ready=ready, enrollment=enrollment):
                    identity = replace(self.identity, enrollment=enrollment, defaults_ready=ready)
                    before = self.facts(state=replace(self.state, signing_identity=identity,
                                                     grub=GrubState()), drivers=drivers)
                    certificate = self.row(before, "certificate")
                    self.assertEqual(certificate.tone, "warning")
                    self.assertEqual(certificate.reason, "signing-defaults-after-enrollment")
                    action = self.assert_action(certificate, "sync-defaults", allowed=False)
                    self.assert_blocker(action, "certificate-enrollment-required", "enrollment")
                    self.assertEqual(self.core.decide(before).next_row, "enrollment")
                    after = self.facts(state=replace(before.secure_boot,
                        signing_identity=replace(identity, enrollment="enrolled")), drivers=drivers)
                    self.assertEqual(self.row(after, "certificate").tone, "warning")
                    self.assertEqual(self.row(after, "enrollment").tone, "success")
                    self.assertEqual(self.core.decide(after).next_row, "certificate")

    def test_broken_defaults_require_configuration_repair_not_another_enrollment(self):
        identity = replace(self.identity, defaults_ready=False)
        facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()))
        self.assertEqual(self.row(facts, "enrollment").tone, "success")
        self.assert_action(self.row(facts, "certificate"), "sync-defaults", allowed=True)
        action = self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)
        self.assert_blocker(action, "signing-defaults-required", "certificate")

    def test_untrusted_drivers_wait_for_verified_unified_signing_defaults(self):
        drivers = DkmsState(modules=("driver.ko",), untrusted_modules=("driver.ko",))
        for enabled in (True, False):
            for ready in (False, None):
                with self.subTest(enabled=enabled, defaults_ready=ready):
                    identity = replace(self.identity, defaults_ready=ready)
                    state = replace(self.state, enabled=enabled,
                                    status=SecureBootStatus.ENABLED if enabled else SecureBootStatus.DISABLED,
                                    signing_identity=identity, grub=GrubState())
                    facts = self.facts(state=state, drivers=drivers)
                    action = self.assert_action(self.row(facts, "drivers"), "repair-dkms", allowed=False)
                    self.assert_blocker(action, "signing-defaults-required", "certificate")
                    self.assertEqual(self.core.decide(facts).next_row, "certificate")

    def test_pending_enrollment_does_not_explain_downstream_defaults_as_another_problem(self):
        identity = replace(self.identity, enrollment="pending", defaults_ready=False)
        facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()))
        action = self.core.assess_action("install-bootloader", facts)
        self.assertEqual([(b.reason, b.row_id) for b in action.blockers],
                         [("certificate-enrollment-required", "enrollment")])

    def test_reboot_guard_uses_actual_chain_evidence_instead_of_cache_signature(self):
        self.assertTrue(hasattr(self.boot, "active_chain"), "Actual ESP evidence must be separate")
        for trust, allowed in (("trusted", True), ("untrusted", False), ("unknown", False)):
            with self.subTest(active_chain=trust):
                state = replace(self.state,
                    signing_identity=replace(self.identity, enrollment="pending", defaults_ready=False),
                    grub=replace(self.boot, active_chain=trust, deployment="pending"))
                action = self.core.assess_action("reboot-enroll", self.facts(state=state))
                self.assertEqual(action.allowed, allowed)

    def test_untrusted_active_chain_can_be_repaired_without_forcing_external_recovery(self):
        self.assertTrue(hasattr(self.boot, "active_chain"))
        facts = self.facts(state=replace(self.state, grub=replace(self.boot, active_chain="untrusted")))
        row = self.row(facts, "bootloader")
        self.assertEqual(row.tone, "error")
        self.assert_action(row, "repair-bootloader", allowed=True)

    def test_unknown_defaults_are_not_treated_as_verified_configuration(self):
        facts = self.facts(state=replace(self.state, grub=GrubState(),
                           signing_identity=replace(self.identity, defaults_ready=None)))
        self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=False)
        certificate = self.row(facts, "certificate")
        self.assertEqual(certificate.tone, "warning")
        self.assert_action(certificate, "refresh", allowed=True)

    def test_unknown_action_is_rejected_without_an_execution_plan(self):
        for action in ("", "reboot", "prepare; id"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.core.assess_action(action, self.facts())

    def test_no_third_party_modules_is_neutral_and_needs_no_repair(self):
        row = self.row(self.facts(drivers=DkmsState()), "drivers")
        self.assertEqual(row.tone, "neutral")
        self.assertIsNone(row.action)

    def test_failed_module_inspection_is_not_the_same_as_no_modules(self):
        row = self.row(self.facts(drivers=DkmsState(), drivers_checked=False), "drivers")
        self.assertEqual(row.tone, "warning")
        self.assert_action(row, "refresh", allowed=True)

    def test_old_trusted_drivers_remain_green_while_new_certificate_is_pending(self):
        drivers = replace(self.drivers, details=(
            ModuleState("example.ko", "/fixture/example.ko", "old-signer", True, False),))
        facts = self.facts(drivers=drivers, state=self.migration_state(
                           replace(self.identity, enrollment="pending", defaults_ready=False)))
        row = self.row(facts, "drivers")
        self.assertEqual(row.tone, "success")
        self.assertIsNone(row.action)

    def test_unsigned_driver_can_be_repaired_with_secure_boot_on_or_off(self):
        drivers = DkmsState(modules=("example.ko",), untrusted_modules=("example.ko",),
                            details=(ModuleState("example.ko", "/fixture/example.ko", None, False),))
        for status, tone in ((SecureBootStatus.ENABLED, "error"), (SecureBootStatus.DISABLED, "warning")):
            with self.subTest(status=status):
                state = replace(self.state, status=status, enabled=status is SecureBootStatus.ENABLED)
                row = self.row(self.facts(state=state, drivers=drivers), "drivers")
                self.assertEqual(row.tone, tone)
                self.assert_action(row, "repair-dkms", allowed=True)

    def test_blocked_drivers_take_priority_over_optional_certificate_upgrade(self):
        identity = replace(self.identity, purpose="module-only", managed=False, defaults_ready=None)
        drivers = DkmsState(modules=("example.ko",), untrusted_modules=("example.ko",))
        facts = self.facts(state=replace(self.state, signing_identity=identity, grub=GrubState()),
                           drivers=drivers)
        self.assertEqual(self.core.decide(facts).next_row, "drivers")
        self.assert_action(self.row(facts, "drivers"), "repair-dkms", allowed=True)

    def test_signed_driver_awaiting_its_certificate_needs_enrollment_not_resigning(self):
        identity = replace(self.identity, enrollment="pending", defaults_ready=False)
        drivers = DkmsState(modules=("example.ko",), untrusted_modules=("example.ko",),
                            details=(ModuleState("example.ko", "/fixture/example.ko", "new-signer", False, True),))
        facts = self.facts(state=replace(self.state, enrolled=False, signing_identity=identity,
                           enrolled_certificate_serials=("old-signer",), grub=GrubState()),
                           drivers=drivers, next_boot=self.evidence(loader_source="ubuntu", modules_trusted=False))
        action = self.assert_action(self.row(facts, "drivers"), "repair-dkms", allowed=False)
        self.assert_blocker(action, "certificate-enrollment-required", "enrollment")

    def test_uninstalled_own_grub_is_recommended_as_the_next_step_in_both_sb_modes(self):
        for status in (SecureBootStatus.ENABLED, SecureBootStatus.DISABLED):
            with self.subTest(status=status):
                state = replace(self.state, status=status,
                                enabled=status is SecureBootStatus.ENABLED, grub=GrubState())
                facts = self.facts(state=state)
                page = self.core.decide(facts)
                row = self.row(facts, "bootloader")
                self.assertEqual(row.tone, "warning")
                self.assert_action(row, "install-bootloader", allowed=True)
                self.assertEqual(page.next_row, "bootloader")
                self.assertFalse(any(row.tone == "error" for row in page.rows))

    def test_prepared_identity_allows_installing_own_grub_with_secure_boot_off(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED, grub=GrubState())
        facts = self.facts(state=state, next_boot=self.evidence(loader_source="ubuntu"))
        self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=True)
        self.assertEqual(self.row(facts, "secure_boot").tone, "neutral")

    def test_installation_requires_valid_key_and_enrollment_in_both_sb_modes(self):
        for status in (SecureBootStatus.ENABLED, SecureBootStatus.DISABLED):
            for valid, enrollment, allowed in (
                (True, "enrolled", True), (True, "absent", False),
                (False, "enrolled", False), (False, "absent", False),
            ):
                with self.subTest(status=status, valid=valid, enrollment=enrollment):
                    identity = replace(self.identity, key_valid=valid, enrollment=enrollment)
                    state = replace(self.state, status=status, enabled=status is SecureBootStatus.ENABLED,
                                    signing_identity=identity, grub=GrubState())
                    facts = self.facts(state=state, next_boot=self.evidence(loader_source="ubuntu"))
                    self.assert_action(self.row(facts, "bootloader"), "install-bootloader", allowed=allowed)

    def test_unsigned_grub_offers_repair_but_blocks_enabling_secure_boot(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED,
                        grub=replace(self.boot, signature="missing"))
        facts = self.facts(state=state, next_boot=self.evidence(loader_trusted=False))
        self.assert_action(self.row(facts, "bootloader"), "repair-bootloader", allowed=True)
        action = self.assert_action(self.row(facts, "secure_boot"), "firmware-setup", allowed=False)
        self.assert_blocker(action, "bootloader-unsigned", "bootloader")

    def test_signed_candidate_does_not_make_unverified_esp_ready_to_enable(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED,
                        grub=replace(self.boot, deployment="pending"))
        facts = self.facts(state=state, next_boot=self.evidence(loader_trusted=None))
        self.assert_action(self.row(facts, "bootloader"), "repair-bootloader", allowed=True)
        self.assert_action(self.row(facts, "secure_boot"), "firmware-setup", allowed=False)

    def test_valid_own_chain_allows_guidance_to_enable_secure_boot(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED)
        self.assert_action(self.row(self.facts(state=state, next_boot=self.evidence()), "secure_boot"),
                           "firmware-setup", allowed=True)

    def test_trusted_ubuntu_chain_does_not_require_own_grub_or_unified_identity(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED, grub=GrubState(),
                        signing_identity=replace(self.identity, purpose="module-only", managed=False))
        facts = self.facts(state=state)
        self.assertEqual(self.row(facts, "bootloader").tone, "warning")
        self.assert_action(self.row(facts, "secure_boot"), "firmware-setup", allowed=True)

    def test_prepared_disabled_system_needs_no_uncollected_next_boot_evidence(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED)
        facts = self.facts(state=state)
        self.assert_action(self.row(facts, "secure_boot"), "firmware-setup", allowed=True)
        self.assertEqual(self.row(facts, "secure_boot").reason, "secure-boot-prepared")
        self.assertIsNone(self.core.decide(facts).next_row)

    def test_verified_owned_fallback_does_not_require_a_named_bootcurrent_entry(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED,
                        boot_loader="unknown")
        facts = self.facts(state=state)
        self.assertEqual(self.row(facts, "bootloader").tone, "success")
        self.assert_action(self.row(facts, "secure_boot"), "firmware-setup", allowed=True)

    def test_firmware_guidance_preserves_real_preparation_blockers(self):
        disabled = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED)
        cases = (
            (replace(disabled, signing_identity=replace(self.identity, enrollment="pending")),
             self.drivers, True, "certificate-pending", "enrollment"),
            (replace(disabled, signing_identity=replace(self.identity, key_valid=False)),
             self.drivers, True, "signing-key-unavailable", "certificate"),
            (replace(disabled, signing_identity=replace(self.identity, defaults_ready=False)),
             self.drivers, True, "signing-defaults-required", "certificate"),
            (disabled, DkmsState(modules=("example.ko",), untrusted_modules=("example.ko",)),
             True, "drivers-untrusted", "drivers"),
            (disabled, DkmsState(), False, "drivers-unknown", "drivers"),
            (replace(disabled, grub=replace(self.boot, active_chain="unknown")),
             self.drivers, True, "bootloader-unknown", "bootloader"),
        )
        for state, drivers, checked, reason, row_id in cases:
            with self.subTest(reason=reason):
                facts = self.facts(state=state, drivers=drivers, drivers_checked=checked)
                action = self.assert_action(self.row(facts, "secure_boot"), "firmware-setup", allowed=False)
                self.assert_blocker(action, reason, row_id)
                self.assertEqual(self.row(facts, row_id).reason, reason)
                self.assertIsNotNone(self.core.decide(facts).next_row)

    def test_bios_mode_retains_six_rows_with_an_explanation(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.UNSUPPORTED,
                        boot_loader="unknown", firmware_reason="legacy-bios")
        facts = self.facts(state=state)
        self.assertEqual(len(self.core.decide(facts).rows), 6)
        self.assertEqual(self.row(facts, "secure_boot").tone, "neutral")
        self.assert_action(self.row(facts, "secure_boot"), "show-firmware-help", allowed=True)
        self.assertNotEqual(self.row(facts, "bootloader").action.id if
                            self.row(facts, "bootloader").action else None, "install-bootloader")

    def test_unknown_firmware_is_rechecked_instead_of_called_disabled(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.UNKNOWN)
        row = self.row(self.facts(state=state), "secure_boot")
        self.assertEqual(row.tone, "warning")
        self.assert_action(row, "refresh", allowed=True)

    def test_unknown_setup_mode_is_visible_as_a_recheck_instead_of_no_repairs(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED, setup_mode=None)
        facts = self.facts(state=state)
        firmware = self.row(facts, "secure_boot")
        self.assertEqual(firmware.reason, "firmware-unknown")
        self.assert_action(firmware, "refresh", allowed=True)
        self.assertEqual(self.core.decide(facts).next_row, "secure_boot")
        self.assertFalse(self.core.assess_action("firmware-setup", facts).allowed)

    def test_setup_mode_needs_platform_key_guidance_not_mok_recreation(self):
        state = replace(self.state, enabled=False, status=SecureBootStatus.DISABLED, setup_mode=True)
        row = self.row(self.facts(state=state, next_boot=self.evidence(firmware_trusted=False)), "secure_boot")
        self.assertEqual(row.tone, "warning")
        self.assert_action(row, "show-firmware-help", allowed=True)

    def test_enrollment_reboot_is_blocked_if_the_active_chain_is_broken(self):
        state = replace(self.state, signing_identity=replace(self.identity, enrollment="pending"),
                        grub=replace(self.boot, active_chain="untrusted"))
        facts = self.facts(state=state, next_boot=self.evidence(loader_trusted=False))
        action = self.assert_action(self.row(facts, "enrollment"), "reboot-enroll", allowed=False)
        self.assert_blocker(action, "boot-recovery-required", "bootloader")
        self.assertEqual(self.core.decide(facts).next_row, "bootloader")
        self.assert_action(self.row(facts, "bootloader"), "show-boot-recovery", allowed=True)

    def test_invalid_candidate_cache_does_not_block_a_proven_old_active_chain(self):
        state = replace(self.state, signing_identity=replace(self.identity, enrollment="pending"),
                        grub=replace(self.boot, signature="invalid", active_chain="trusted"))
        self.assertTrue(self.core.assess_action("reboot-enroll", self.facts(state=state)).allowed)

    def test_key_loss_does_not_make_an_existing_verified_signature_invalid(self):
        identity = replace(self.identity, key_present=False, key_valid=False)
        facts = self.facts(state=replace(self.state, signing_identity=identity), next_boot=self.evidence())
        self.assertEqual(self.row(facts, "bootloader").tone, "success")
        self.assertEqual(self.row(facts, "drivers").tone, "success")
        self.assertNotEqual(self.row(facts, "certificate").tone, "success")

    def test_efi_filename_does_not_change_the_business_decision(self):
        pages = [self.core.decide(self.facts(next_boot=self.evidence(
                    loader_path=rf"\EFI\AnduinOS\shim{suffix}.efi"))) for suffix in ("x64", "aa64")]
        decisions = [[(row.id, row.tone, row.reason,
                       row.action.id if row.action else None) for row in page.rows] for page in pages]
        self.assertEqual(decisions[0], decisions[1])

    def test_attention_rows_always_offer_an_action_and_blockers_point_to_existing_rows(self):
        for identity in (
            IdentityStatus(purpose="missing", enrollment="absent"),
            replace(self.identity, purpose="module-only"),
            replace(self.identity, enrollment="pending", defaults_ready=False),
            replace(self.identity, enrollment="unknown"),
            replace(self.identity, key_valid=False),
        ):
            with self.subTest(purpose=identity.purpose, enrollment=identity.enrollment,
                              key_valid=identity.key_valid):
                page = self.core.decide(self.facts(state=self.migration_state(identity),
                                                  next_boot=self.evidence(loader_source="ubuntu")))
                row_ids = {row.id for row in page.rows}
                for row in page.rows:
                    if row.tone in {"warning", "error"}:
                        self.assertIsNotNone(row.action, f"No next step for {row.id}")
                    if row.action and not row.action.allowed:
                        self.assertTrue(row.action.blockers)
                        for blocker in row.action.blockers:
                            self.assertIn(blocker.row_id, row_ids)

    def test_action_preflight_and_row_share_the_same_enrollment_blocker(self):
        facts = self.facts(state=replace(self.state, grub=GrubState(),
                           signing_identity=replace(self.identity, enrollment="absent")))
        preflight = self.core.assess_action("install-bootloader", facts)
        self.assertFalse(preflight.allowed)
        self.assert_blocker(preflight, "certificate-enrollment-required", "enrollment")
        self.assertEqual(self.row(facts, "bootloader").action, preflight)

    def test_rechecking_does_not_execute_operations_or_mutate_supplied_facts(self):
        facts = self.facts(next_boot=self.evidence())
        original = deepcopy(facts)
        with patch("subprocess.run", side_effect=AssertionError("decide must not invoke system commands")), \
             patch("anduinos_secureboot.operations.execute", side_effect=AssertionError("inspection must not repair")):
            first = self.core.decide(facts)
            second = self.core.decide(facts)
        self.assertEqual(first, second)
        self.assertEqual(facts, original)


if __name__ == "__main__":
    unittest.main()
