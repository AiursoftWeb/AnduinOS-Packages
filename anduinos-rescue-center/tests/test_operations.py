import subprocess
import tempfile
import unittest
from pathlib import Path

from anduinos_rescue_center.operations import reset_password_in_root


class PasswordResetTests(unittest.TestCase):
    def system_root(self, directory):
        root = Path(directory)
        (root / "etc").mkdir()
        (root / "etc/passwd").write_text(
            "root:x:0:0:root:/root:/bin/bash\n"
            "alice:x:1000:1000:Alice:/home/alice:/bin/bash\n",
            encoding="utf-8",
        )
        (root / "etc/shadow").write_text(
            "root:!:1:0:99999:7:::\nalice:!:1:0:99999:7:::\n",
            encoding="utf-8",
        )
        return root

    def test_password_is_never_placed_in_process_arguments(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = self.system_root(directory)
            reset_password_in_root(root, "alice", "correct horse", run=run)
        self.assertEqual(calls[0][0][:2], ["/usr/sbin/chpasswd", "--root"])
        self.assertNotIn("correct horse", calls[0][0])
        self.assertEqual(calls[0][1]["input"], "alice:correct horse\n")
        self.assertEqual(calls[1][0][0], "sync")

    def test_rejects_unknown_or_malformed_accounts_before_chpasswd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.system_root(directory)
            for username in ("missing", "../root", "Alice"):
                with self.subTest(username=username), self.assertRaises((ValueError, RuntimeError)):
                    reset_password_in_root(root, username, "new password")

    def test_rejects_newline_in_password(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.system_root(directory)
            with self.assertRaises(ValueError):
                reset_password_in_root(root, "alice", "first\nroot:changed")
