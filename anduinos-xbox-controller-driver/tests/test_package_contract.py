"""Run lifecycle scripts against a fake DKMS without host side effects."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DkmsLifecycleTests(unittest.TestCase):
    def test_pinned_source_and_lifecycle_versions_match(self):
        downloader = (ROOT / "download.sh").read_text(encoding="utf-8")
        commit = re.search(r'^COMMIT_ID="([0-9a-f]{40})"$', downloader, re.MULTILINE)
        version = re.search(r'^VERSION="([^"]+)"$', downloader, re.MULTILINE)
        self.assertIsNotNone(commit)
        self.assertIsNotNone(version)
        self.assertTrue(version.group(1).endswith("-g" + commit.group(1)[:7]))
        for name in ("postinst.sh", "prerm.sh"):
            with self.subTest(script=name):
                script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn(f'VERSION="{version.group(1)}"', script)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "commands"
        self.modules = self.root / "modules"
        (self.modules / "test-running-kernel" / "build" / "include").mkdir(parents=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        programs = {
            "uname": '#!/bin/sh\nprintf "%s\\n" test-running-kernel\n',
            "dkms": """#!/bin/sh
printf '%s\\n' "$*" >> "$DKMS_TEST_LOG"
if [ "$1" = "$DKMS_FAIL_ACTION" ]; then
    [ -n "$DKMS_FAIL_KERNEL" ] || exit 37
    case " $* " in
        *" $DKMS_FAIL_KERNEL "*) exit 37 ;;
    esac
fi
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

    def run_script(self, script, action, *, all_status="", kernel_status="", failure="", failure_kernel=""):
        self.log.unlink(missing_ok=True)
        # Redirect only the kernel directory to an isolated installed-system
        # fixture; the production script does not accept path overrides.
        candidate = self.root / script
        candidate.write_text((ROOT / "scripts" / script).read_text().replace(
            "/lib/modules/*", str(self.modules / "*")))
        result = subprocess.run(
            [
                "/bin/sh", str(candidate), action,
            ],
            env={
                **os.environ, "PATH": str(self.root / "bin"),
                "DKMS_TEST_LOG": str(self.log), "DKMS_FAIL_ACTION": failure,
                "DKMS_FAIL_KERNEL": failure_kernel,
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

    def test_failure_on_new_kernel_is_not_hidden_by_success_on_old_kernel(self):
        (self.modules / "test-z-newer-kernel" / "build" / "include").mkdir(parents=True)
        result, calls = self.run_script(
            "postinst.sh", "configure", failure="build", failure_kernel="test-z-newer-kernel")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertTrue(any(call.startswith("install ") and "-k test-running-kernel" in call
                            for call in calls))
        self.assertFalse(any(call.startswith("install ") and "-k test-z-newer-kernel" in call
                             for call in calls))

    def test_registered_driver_is_built_for_every_installed_kernel_with_headers(self):
        for kernel in ("test-older-kernel", "test-newer-kernel"):
            (self.modules / kernel / "build" / "include").mkdir(parents=True)
        (self.modules / "test-without-headers").mkdir()
        result, calls = self.run_script("postinst.sh", "configure", all_status="registered")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [call.split()[0] for call in calls]
        self.assertNotIn("add", actions)
        self.assertEqual(actions[-2:], ["build", "install"])
        for action in ("build", "install"):
            kernels = [call.split()[call.split().index("-k") + 1]
                       for call in calls if call.split()[0] == action]
            self.assertCountEqual(kernels, ["test-running-kernel", "test-older-kernel", "test-newer-kernel"])
        self.assertIn("test-without-headers: kernel headers are not installed", result.stdout)

    def test_chroot_uses_installed_kernel_instead_of_host_kernel(self):
        (self.modules / "test-running-kernel").rename(self.modules / "test-target-kernel")
        result, calls = self.run_script("postinst.sh", "configure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("-k test-target-kernel" in call for call in calls))
        self.assertFalse(any("test-running-kernel" in call for call in calls))

    def test_installed_driver_is_not_rebuilt_and_built_driver_only_installs(self):
        for state, expected in (("installed", ["status", "status"]), ("built", ["status", "status", "install"])):
            with self.subTest(state=state):
                result, calls = self.run_script(
                    "postinst.sh", "configure", all_status="registered",
                    kernel_status="module/version, test-running-kernel, arch: " + state,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call.split()[0] for call in calls], expected)

    def test_failed_upgrade_does_not_hide_the_old_cleanup_failure(self):
        result, calls = self.run_script("prerm.sh", "failed-upgrade")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retry the upgrade", result.stderr)
        self.assertEqual(calls, [])

    def test_removal_is_idempotent_and_dkms_failure_is_not_hidden(self):
        result, calls = self.run_script("prerm.sh", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call.split()[0] for call in calls], ["status"])
        result, calls = self.run_script("prerm.sh", "remove", all_status="registered", failure="remove")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual([call.split()[0] for call in calls], ["status", "remove"])


if __name__ == "__main__":
    unittest.main()
