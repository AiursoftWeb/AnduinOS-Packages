"""Credential changes preserve other roles across interruption boundaries."""

import copy
import unittest
from unittest.mock import patch

from anduinos_driver_center.encryption_credentials import (
    CredentialError, TOKEN_TYPE, change, managed_token,
)

PASSWORD = "old-password"
RECOVERY = "a" * 48
MACHINE = b"test-machine-key" * 4


class PowerLoss(BaseException):
    pass


class MemoryVolume:
    def __init__(self):
        self.machine = MACHINE
        self.keys = {"0": PASSWORD, "1": RECOVERY, "2": MACHINE}
        self.tokens = {}
        self.events = []
        self.stop_after = None

    def header(self):
        return copy.deepcopy({"keyslots": {key: {} for key in self.keys}, "tokens": self.tokens})

    def test(self, slot, value):
        return self.keys.get(slot) == value

    def event(self, name):
        self.events.append(name)
        if len(self.events) == self.stop_after:
            raise PowerLoss()

    def store(self, number, token, header):
        if header != self.header():
            raise CredentialError("layout-changed")
        self.tokens[number] = copy.deepcopy(token)
        self.event("token-" + (token["pending"]["phase"] if token["pending"] else "complete"))

    def add(self, slot, password_slot, current, replacement):
        assert slot not in self.keys and self.test(password_slot, current)
        self.keys[slot] = replacement
        self.event("add")

    def remove(self, slot):
        token = managed_token(self.header())[1]
        assert slot not in token["roles"].values(), "Removed a current role"
        assert len(self.keys) >= 4, "Removed a last credential"
        del self.keys[slot]
        self.event("remove")


def request(role="password", current=PASSWORD, replacement="new-password"):
    return dict(role=role, current=current, replacement=replacement, legacy_recovery=RECOVERY)


class CredentialTests(unittest.TestCase):
    def test_password_and_recovery_changes_preserve_other_credentials(self):
        volume = MemoryVolume()
        change(volume, request())
        token = managed_token(volume.header())[1]
        self.assertEqual(token["type"], TOKEN_TYPE)
        self.assertTrue(volume.test(token["roles"]["password"], "new-password"))
        self.assertTrue(volume.test(token["roles"]["recovery"], RECOVERY))
        self.assertEqual(volume.keys["2"], MACHINE)
        self.assertNotIn(PASSWORD, volume.keys.values())
        change(volume, request("recovery", "new-password", "b" * 48))
        token = managed_token(volume.header())[1]
        self.assertTrue(volume.test(token["roles"]["password"], "new-password"))
        self.assertTrue(volume.test(token["roles"]["recovery"], "b" * 48))
        self.assertNotIn(RECOVERY, volume.keys.values())
        self.assertEqual(volume.keys["2"], MACHINE)
        self.assertEqual(len(volume.keys), 3)

    def test_preview_adoption_requires_all_three_roles_before_writing(self):
        for values, error in (
            (request(current="incorrect"), "wrong-password"),
            (dict(request(), legacy_recovery=""), "setup-required"),
            (dict(request(), legacy_recovery="incorrect"), "wrong-recovery-key"),
        ):
            with self.subTest(error=error):
                volume = MemoryVolume()
                before = volume.header()
                with self.assertRaisesRegex(CredentialError, error):
                    change(volume, values)
                self.assertEqual(volume.header(), before)
                self.assertFalse(volume.events)
        volume = MemoryVolume()
        volume.keys["2"] = b"different-machine"
        with self.assertRaisesRegex(CredentialError, "machine-unavailable"):
            change(volume, request())
        self.assertFalse(volume.events)

    def test_a_shared_password_and_recovery_secret_is_not_adopted(self):
        volume = MemoryVolume()
        volume.keys["1"] = PASSWORD
        with self.assertRaisesRegex(CredentialError, "unsupported-layout"):
            change(volume, dict(request(), legacy_recovery=PASSWORD))
        self.assertFalse(volume.events)

    def test_wrong_password_reuse_and_foreign_layout_do_not_remove_credentials(self):
        volume = MemoryVolume()
        change(volume, request())
        for values, error in (
            (request(current=PASSWORD), "wrong-password"),
            (request(current="new-password", replacement=RECOVERY), "credential-reused"),
            (dict(request(current="new-password"), role="machine"), "invalid-request"),
        ):
            with self.subTest(error=error):
                before = volume.header()
                with self.assertRaisesRegex(CredentialError, error):
                    change(volume, values)
                self.assertEqual(volume.header(), before)
        volume.keys["9"] = "externally-added"
        with self.assertRaisesRegex(CredentialError, "unsupported-layout"):
            change(volume, request(current="new-password"))
        self.assertEqual(volume.keys["9"], "externally-added")

    def test_each_durable_boundary_can_resume_without_losing_a_role(self):
        # Initial adoption, prepare, add, promote, retire old, clear pending.
        for role in ("password", "recovery"):
            for boundary in range(1, 7):
                with self.subTest(role=role, boundary=boundary):
                    volume = MemoryVolume()
                    volume.stop_after = boundary
                    replacement = "new-password" if role == "password" else "b" * 48
                    with self.assertRaises(PowerLoss):
                        change(volume, request(role=role, replacement=replacement))
                    self.assertIn(MACHINE, volume.keys.values())
                    self.assertGreaterEqual(len(volume.keys), 3)
                    volume.stop_after = None
                    token = managed_token(volume.header())[1]
                    current = volume.keys[token["roles"]["password"]]
                    final = "third-password" if role == "password" else "c" * 48
                    change(volume, request(role=role, current=current, replacement=final))
                    expected = {final, RECOVERY, MACHINE} if role == "password" else {PASSWORD, final, MACHINE}
                    self.assertEqual(set(volume.keys.values()), expected)
                    self.assertIsNone(managed_token(volume.header())[1]["pending"])

    def test_failed_new_key_verification_keeps_old_and_machine_usable(self):
        volume = MemoryVolume()
        original = volume.test
        with patch.object(volume, "test", side_effect=lambda slot, value: False if slot == "3" else original(slot, value)):
            with self.assertRaisesRegex(CredentialError, "verification-failed"):
                change(volume, request())
        self.assertTrue(volume.test("0", PASSWORD))
        self.assertTrue(volume.test("2", MACHINE))
        self.assertNotIn("remove", volume.events)

    def test_repeated_changes_reuse_free_slots_without_accumulating_keys(self):
        volume = MemoryVolume()
        current = PASSWORD
        for number in range(8):
            replacement = f"new-password-{number}"
            change(volume, request(current=current, replacement=replacement))
            self.assertEqual(len(volume.keys), 3)
            self.assertEqual(volume.keys["2"], MACHINE)
            current = replacement

    def test_corrupt_or_foreign_role_metadata_is_rejected(self):
        volume = MemoryVolume()
        change(volume, request())
        for alter in (
            lambda token: token.update(version=2),
            lambda token: token["roles"].update(password="2"),
            lambda token: token.update(pending={"role": "machine"}),
            lambda token: token.update(keyslots=[]),
        ):
            header = volume.header()
            alter(header["tokens"]["0"])
            with self.assertRaises(CredentialError):
                managed_token(header)
