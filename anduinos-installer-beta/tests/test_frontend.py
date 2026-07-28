import io
import unittest
from unittest.mock import patch

from frontend import ExecutorClient, FrontendPlanError, create_install_plan
from installer_core.model import Architecture, DiskIdentity, Firmware, SecureBoot
from installer_core.probe import PlatformProbe


def state():
    return {
        "lang": "en",
        "locale": "en_US.UTF-8",
        "keyboard": "us",
        "disk": "/dev/sda",
        "disk_size_bytes": 64 * 1024**3,
        "disk_stable_id": "serial:test",
        "disk_model": "Test",
        "filesystem": "btrfs",
        "username": "alice",
        "full_name": "Alice Example",
        "password": "plaintext-secret",
        "hostname": "anduinos",
        "timezone": "Asia/Singapore",
    }


class FrontendPlanTests(unittest.TestCase):
    def make_plan(self):
        values = state()
        disk = DiskIdentity(
            "/dev/sda", "serial:test", 64 * 1024**3, "Test", "test"
        )
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch("frontend.probe_disks", return_value=(disk,)),
            patch("frontend.probe_platform", return_value=platform),
        ):
            return create_install_plan(values)

    def test_reprobes_disk_hashes_password_and_clears_all_plaintext(self):
        values = state()
        cleared = []
        values["_clear_password_ui"] = lambda: cleared.append(True)
        disk = DiskIdentity(
            "/dev/sda", "serial:test", 64 * 1024**3, "Test", "test"
        )
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch("frontend.probe_disks", return_value=(disk,)),
            patch("frontend.probe_platform", return_value=platform),
        ):
            plan = create_install_plan(values)
        self.assertEqual(values["password"], "")
        self.assertNotIn("_clear_password_ui", values)
        self.assertEqual(cleared, [True])
        self.assertNotIn("plaintext-secret", repr(plan))
        self.assertEqual(plan.identity.password_hash, "$6$salt$hash")

    def test_executor_client_sends_one_plan_and_maps_json_events(self):
        class CapturingInput:
            def __init__(self):
                self.value = ""
                self.closed = False

            def write(self, value):
                self.value += value

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self):
                self.stdin = CapturingInput()
                self.stdout = io.StringIO(
                    '{"event":"log","message":"Preparing"}\n'
                    '{"event":"progress","step":"partition","done":2,"total":9}\n'
                    '{"event":"complete","error":""}\n'
                )
                self.stderr = io.StringIO("")

            def wait(self):
                return 0

        process = FakeProcess()
        with (
            patch("frontend.os.geteuid", return_value=1000),
            patch("frontend.subprocess.Popen", return_value=process) as popen,
        ):
            logs = []
            progress = []
            succeeded, error = ExecutorClient("/test/executor").run(
                self.make_plan(),
                logs.append,
                lambda step, done, total: progress.append((step, done, total)),
            )

        command = popen.call_args.args[0]
        self.assertEqual(command[0], "systemd-inhibit")
        self.assertEqual(command[-3:], ["sudo", "--non-interactive", "/test/executor"])
        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.stdin.value.count("\n"), 1)
        self.assertNotIn("plaintext-secret", process.stdin.value)
        self.assertEqual(logs, ["Preparing"])
        self.assertEqual(progress, [("partition", 2, 9)])
        self.assertTrue(succeeded)
        self.assertEqual(error, "")

    def test_rejects_disk_that_changed_after_selection(self):
        values = state()
        replacement = DiskIdentity(
            "/dev/sda", "serial:replacement", 64 * 1024**3
        )
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch("frontend.probe_disks", return_value=(replacement,)),
        ):
            with self.assertRaisesRegex(FrontendPlanError, "changed"):
                create_install_plan(values)
        self.assertEqual(values["password"], "")
