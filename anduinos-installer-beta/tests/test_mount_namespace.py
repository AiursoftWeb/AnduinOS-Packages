import subprocess
import unittest
from pathlib import Path

from installer_core.mount_namespace import isolate_mount_namespace


class MountNamespaceTests(unittest.TestCase):
    def test_executor_isolates_before_reading_the_plan(self):
        source = (
            Path(__file__).parents[1] / "src/executor_cli.py"
        ).read_text()
        self.assertLess(
            source.index("isolate_mount_namespace()"),
            source.index("sys.stdin.readline()"),
        )

    def test_unshares_then_makes_the_complete_tree_private(self):
        calls = []

        def unshare(flag):
            calls.append(("unshare", flag))

        def run(command, **kwargs):
            calls.append(("run", tuple(command), kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        isolate_mount_namespace(unshare=unshare, run=run)

        self.assertEqual(calls[0][0], "unshare")
        self.assertEqual(
            calls[1][1],
            ("/usr/bin/mount", "--make-rprivate", "/"),
        )

    def test_private_propagation_failure_is_fatal(self):
        def run(command, **_kwargs):
            return subprocess.CompletedProcess(
                command, 1, "", "permission denied"
            )

        with self.assertRaisesRegex(RuntimeError, "permission denied"):
            isolate_mount_namespace(unshare=lambda _flag: None, run=run)
