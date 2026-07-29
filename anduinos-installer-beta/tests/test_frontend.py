import io
import unittest
from unittest.mock import patch

from frontend import (
    DevelopmentExecutorClient,
    ExecutorClient,
    FrontendPlanError,
    create_install_plan,
)
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
        "password_confirmation": "plaintext-secret",
        "passwordless_shared": False,
        "sudo_without_password": False,
        "hostname": "anduinos",
        "timezone": "Asia/Singapore",
        "install_updates": True,
        "install_third_party_drivers": False,
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
        self.assertEqual(values["password_confirmation"], "")
        self.assertNotIn("_clear_password_ui", values)
        self.assertEqual(cleared, [True])
        self.assertNotIn("plaintext-secret", repr(plan))
        self.assertEqual(plan.identity.password_hash, "$6$salt$hash")

    def test_rejects_mismatched_password_confirmation(self):
        values = state()
        values["password_confirmation"] = "different-secret"
        with self.assertRaisesRegex(FrontendPlanError, "do not match"):
            create_install_plan(values)
        self.assertEqual(values["password"], "")
        self.assertEqual(values["password_confirmation"], "")

    def test_passwordless_plan_never_hashes_or_carries_a_password(self):
        values = state()
        values["password"] = ""
        values["password_confirmation"] = ""
        values["passwordless_shared"] = True
        values["sudo_without_password"] = True
        disk = DiskIdentity(
            "/dev/sda", "serial:test", 64 * 1024**3, "Test", "test"
        )
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        with (
            patch(
                "frontend.hash_password",
                side_effect=AssertionError("must not hash an empty password"),
            ),
            patch("frontend.probe_disks", return_value=(disk,)),
            patch("frontend.probe_platform", return_value=platform),
        ):
            plan = create_install_plan(values)
        self.assertEqual(plan.identity.authentication.value, "passwordless-shared")
        self.assertEqual(plan.identity.password_hash, "")
        self.assertTrue(plan.identity.sudo_without_password)

    def test_empty_password_without_passwordless_sudo_is_rejected(self):
        values = state()
        values["password"] = ""
        values["password_confirmation"] = ""
        values["passwordless_shared"] = False
        values["sudo_without_password"] = False
        with self.assertRaisesRegex(
            FrontendPlanError, "requires passwordless sudo"
        ):
            create_install_plan(values)
        self.assertEqual(values["password"], "")
        self.assertEqual(values["password_confirmation"], "")

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
                    '{"event":"step-status","step":"partition",'
                    '"status":"running","message":""}\n'
                    '{"event":"step-status","step":"partition",'
                    '"status":"succeeded","message":""}\n'
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
            statuses = []
            succeeded, error = ExecutorClient("/test/executor").run(
                self.make_plan(),
                logs.append,
                lambda step, done, total: progress.append((step, done, total)),
                lambda step, status, message: statuses.append(
                    (step, status, message)
                ),
            )

        command = popen.call_args.args[0]
        self.assertEqual(command[0], "systemd-inhibit")
        self.assertEqual(command[-3:], ["sudo", "--non-interactive", "/test/executor"])
        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.stdin.value.count("\n"), 1)
        self.assertNotIn("plaintext-secret", process.stdin.value)
        self.assertEqual(logs, ["Preparing"])
        self.assertEqual(progress, [("partition", 2, 9)])
        self.assertEqual(
            statuses,
            [
                ("partition", "running", ""),
                ("partition", "succeeded", ""),
            ],
        )
        self.assertTrue(succeeded)
        self.assertEqual(error, "")

    def test_development_client_never_starts_a_process(self):
        logs = []
        progress = []
        statuses = []
        with (
            patch(
                "frontend.subprocess.Popen",
                side_effect=AssertionError("must not start a process"),
            ),
            patch("frontend.time.sleep"),
        ):
            succeeded, error = DevelopmentExecutorClient().run(
                self.make_plan(),
                logs.append,
                lambda step, done, total: progress.append((step, done, total)),
                lambda step, status, message: statuses.append(
                    (step, status, message)
                ),
            )
        self.assertTrue(succeeded)
        self.assertEqual(error, "")
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], "complete")
        self.assertTrue(any("privileged executor is disabled" in item for item in logs))
        self.assertTrue(any("No disk" in item for item in logs))
        self.assertTrue(statuses)
        for index in range(0, len(statuses), 2):
            self.assertEqual(statuses[index][1], "running")
            self.assertEqual(statuses[index + 1][1], "succeeded")
        simulated = "\n".join(logs)
        self.assertIn("[refresh-package-indexes]", simulated)
        self.assertIn("[upgrade-system]", simulated)
        self.assertNotIn("[install-third-party-drivers]", simulated)
        step_order = [
            step for step, status, _message in statuses
            if status == "running"
        ]
        self.assertLess(
            step_order.index("prepare-secure-boot"),
            step_order.index("refresh-package-indexes"),
        )

    def test_development_pipeline_honors_software_choices(self):
        values = state()
        values["install_updates"] = False
        values["install_third_party_drivers"] = True
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
        logs = []
        with patch("frontend.time.sleep"):
            succeeded, error = DevelopmentExecutorClient().run(
                plan, logs.append, lambda *_args: None
            )
        self.assertTrue(succeeded)
        self.assertEqual(error, "")
        simulated = "\n".join(logs)
        self.assertNotIn("[refresh-package-indexes]", simulated)
        self.assertNotIn("[upgrade-system]", simulated)
        self.assertIn("[install-third-party-drivers]", simulated)

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
