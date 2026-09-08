"""Run lifecycle scripts against a fake DKMS in a read-only bubblewrap sandbox."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DkmsLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "commands"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        programs = {
            "uname": '#!/bin/sh\nprintf "%s\\n" test-running-kernel\n',
            "dkms": """#!/bin/sh
printf '%s\\n' "$*" >> "$DKMS_TEST_LOG"
[ "$1" != "$DKMS_FAIL_ACTION" ] || exit 37
if [ "$1" = status ]; then
    case " $* " in
        *" -k "*) printf '%s' "$DKMS_KERNEL_STATUS" ;;
        *) printf '%s' "$DKMS_ALL_STATUS" ;;
    esac
fi
""",
        }
        for name, body in programs.items():
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)

    def run_script(self, script, action, *, all_status="", kernel_status="", failure=""):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            [
                "bwrap", "--unshare-all", "--die-with-parent",
                "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--bind", str(self.root), str(self.root),
                "--", "/bin/sh", str(ROOT / "scripts" / script), action,
            ],
            env={
                **os.environ, "PATH": str(self.root / "bin") + ":/usr/bin:/bin",
                "DKMS_TEST_LOG": str(self.log), "DKMS_FAIL_ACTION": failure,
                "DKMS_ALL_STATUS": all_status, "DKMS_KERNEL_STATUS": kernel_status,
            },
            capture_output=True, text=True, timeout=15,
        )
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, calls

    def test_failed_dkms_step_stops_configuration_before_any_later_step(self):
        for failure in ("status", "add", "build", "install"):
            with self.subTest(failure=failure):
                result, calls = self.run_script("postinst.sh", "configure", failure=failure)
                self.assertEqual(result.returncode, 37, result.stderr)
                actions = [call.split()[0] for call in calls]
                self.assertEqual(actions[-1], failure)
                if failure in ("status", "add", "build"):
                    self.assertNotIn("install", actions)

    def test_registered_driver_is_built_only_for_the_running_kernel(self):
        result, calls = self.run_script("postinst.sh", "configure", all_status="registered")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [call.split()[0] for call in calls]
        self.assertNotIn("add", actions)
        self.assertEqual(actions[-2:], ["build", "install"])
        for call in calls[-2:]:
            arguments = call.split()
            self.assertEqual(arguments[arguments.index("-k") + 1], "test-running-kernel")

    def test_installed_driver_is_not_rebuilt_and_built_driver_only_installs(self):
        for state, expected in (("installed", ["status", "status"]), ("built", ["status", "status", "install"])):
            with self.subTest(state=state):
                result, calls = self.run_script(
                    "postinst.sh", "configure", all_status="registered",
                    kernel_status="module/version, test-running-kernel, arch: " + state,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call.split()[0] for call in calls], expected)

    def test_removal_is_idempotent_and_dkms_failure_is_not_hidden(self):
        result, calls = self.run_script("prerm.sh", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call.split()[0] for call in calls], ["status"])
        result, calls = self.run_script("prerm.sh", "remove", all_status="registered", failure="remove")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual([call.split()[0] for call in calls], ["status", "remove"])


if __name__ == "__main__":
    unittest.main()
