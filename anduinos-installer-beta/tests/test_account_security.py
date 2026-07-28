import unittest

from installer_core.account_security import (
    AccountNextAction,
    account_next_action,
)


class AccountSecurityDecisionTests(unittest.TestCase):
    def test_password_and_normal_sudo_continues(self):
        self.assertIs(
            account_next_action("secret1", "secret1", False),
            AccountNextAction.CONTINUE,
        )

    def test_password_and_passwordless_sudo_requires_confirmation(self):
        self.assertIs(
            account_next_action("secret1", "secret1", True),
            AccountNextAction.CONFIRM_SUDO,
        )

    def test_empty_password_and_normal_sudo_blocks_lockout(self):
        self.assertIs(
            account_next_action("", "", False),
            AccountNextAction.BLOCK_LOCKOUT,
        )

    def test_empty_password_and_passwordless_sudo_requires_strong_confirmation(self):
        self.assertIs(
            account_next_action("", "", True),
            AccountNextAction.CONFIRM_PASSWORDLESS_SUDO,
        )

    def test_mismatched_passwords_cannot_be_decided(self):
        with self.assertRaisesRegex(ValueError, "must match"):
            account_next_action("secret1", "different", False)
