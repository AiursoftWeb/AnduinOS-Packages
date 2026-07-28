import subprocess
import unittest

from installer_core.passwords import PasswordHashError, hash_password


class PasswordHashTests(unittest.TestCase):
    def test_password_is_passed_on_stdin_not_argv(self):
        captured = {}

        def run(command, **kwargs):
            captured["command"] = command
            captured["input"] = kwargs["input"]
            return subprocess.CompletedProcess(
                command, 0, "$6$salt$hash\n", ""
            )

        result = hash_password("correct horse battery staple", run=run)
        self.assertEqual(result, "$6$salt$hash")
        self.assertNotIn("correct horse battery staple", repr(captured["command"]))
        self.assertEqual(captured["input"], "correct horse battery staple\n")

    def test_rejects_multiline_password(self):
        with self.assertRaises(PasswordHashError):
            hash_password("safe\nroot:injected")

