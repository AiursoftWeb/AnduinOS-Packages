"""Run theme lifecycle scripts with boot-writing commands isolated."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "calls"
        self.writer = self.root / "writer"
        self.program("writer", 'printf "writer %s\\n" "$*" >> "$CALLS"\n'
                     'exit "${WRITER_EXIT:-0}"\n')
        self.program("update-alternatives", 'printf "alternatives %s\\n" "$*" >> "$CALLS"\n')
        self.program("dracut", 'printf "UNSAFE dracut %s\\n" "$*" >> "$CALLS"\nexit 99\n')

    def program(self, name, source):
        path = self.root / name
        path.write_text("#!/bin/sh\nset -eu\n" + source)
        path.chmod(0o755)

    def run_script(self, name, action, writer_exit=0):
        self.log.unlink(missing_ok=True)
        source = (ROOT / "scripts" / name).read_text().replace(
            "/usr/libexec/anduinos-dracut-verify", str(self.writer))
        result = subprocess.run(
            ["/bin/sh", "-s", "--", action], input=source,
            env={**os.environ, "PATH": f"{self.root}:/usr/bin:/bin",
                 "CALLS": str(self.log), "WRITER_EXIT": str(writer_exit)},
            capture_output=True, text=True, timeout=10,
        )
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, calls

    def test_configuration_registers_themes_and_rebuilds(self):
        result, calls = self.run_script("postinst.sh", "configure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([call for call in calls if call.startswith("alternatives ")]), 4)
        self.assertEqual(calls[-1], "writer --rebuild")

    def test_missing_writer_fails_before_modifying_themes(self):
        self.writer.unlink()
        result, calls = self.run_script("postinst.sh", "configure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required initrd writer is missing", result.stderr)
        self.assertEqual(calls, [])

    def test_rebuild_failure_propagates_and_configuration_can_be_retried(self):
        for name, action in (("postinst.sh", "configure"), ("prerm.sh", "remove"),
                             ("prerm.sh", "deconfigure")):
            with self.subTest(script=name, action=action):
                result, calls = self.run_script(name, action, writer_exit=31)
                self.assertEqual(result.returncode, 31, result.stderr)
                self.assertEqual(calls[-1], "writer --rebuild")
                result, calls = self.run_script(name, action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[-1], "writer --rebuild")

    def test_removal_still_works_after_tools_are_lost(self):
        self.writer.unlink()
        for action in ("remove", "deconfigure"):
            with self.subTest(action=action):
                result, calls = self.run_script("prerm.sh", action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("existing boot images retained", result.stderr)
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(call.startswith("alternatives --remove ") for call in calls))

    def test_upgrade_and_abort_do_not_rebuild(self):
        for name, action in (("prerm.sh", "upgrade"), ("prerm.sh", "failed-upgrade"),
                             ("postinst.sh", "abort-upgrade")):
            with self.subTest(script=name, action=action):
                result, calls = self.run_script(name, action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
