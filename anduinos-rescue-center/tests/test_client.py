import json
import subprocess
import unittest
from unittest.mock import patch

from anduinos_rescue_center.client import (
    HELPER,
    LIVE_HELPER,
    create_snapshot,
    export_file,
    inspect_target,
    list_files,
    probe,
    reset_password,
    restore_snapshot,
)


class ClientTests(unittest.TestCase):
    def test_calls_only_fixed_helper_action(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({"schema": 1}), "")

        self.assertEqual(probe(run=run)["schema"], 1)
        self.assertEqual(calls, [["pkexec", HELPER, "probe"]])

    def test_live_session_uses_restricted_passwordless_helper(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"schema": 1}', "")

        with patch(
            "anduinos_rescue_center.client.is_live_environment", return_value=True
        ):
            probe(run=run)
        self.assertEqual(calls, [["pkexec", LIVE_HELPER, "probe"]])

    def test_rejects_incompatible_helper_output(self):
        def run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, '{"schema": 99}', "")

        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            probe(run=run)

    def test_target_inspection_passes_bound_identity_to_fixed_helper(self):
        calls = []
        identity = "a" * 64

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"schema": 1}', "")

        inspect_target("/dev/sda2", identity, run=run)
        self.assertEqual(
            calls, [["pkexec", HELPER, "inspect", "/dev/sda2", identity]]
        )

    def test_target_inspection_rejects_unbound_input_before_privilege(self):
        with self.assertRaises(ValueError):
            inspect_target("/tmp/not-a-device", "short")

    def test_password_is_sent_over_stdin_not_command_arguments(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs.get("input")))
            return subprocess.CompletedProcess(command, 0, "", "")

        reset_password("/dev/sda2", "a" * 64, "alice", "secret words", run=run)
        command, standard_input = calls[0]
        self.assertNotIn("secret words", command)
        self.assertEqual(standard_input, "secret words")

    def test_file_actions_keep_paths_as_separate_arguments(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            response = {"schema": 1, "entries": []}
            if "export" in command:
                response["exported"] = "/home/live/report.txt"
            return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

        list_files("/dev/sda2", "a" * 64, "home/alice/My Files", run=run)
        exported = export_file(
            "/dev/sda2", "a" * 64, "home/alice/report.txt", "/home/live", run=run
        )
        self.assertEqual(exported, "/home/live/report.txt")
        self.assertEqual(calls[0][-1], "home/alice/My Files")
        self.assertEqual(calls[1][-1], "/home/live")

    def test_snapshot_actions_use_fixed_helper_and_explicit_protection(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"schema": 1}', "")

        identity = "a" * 64
        create_snapshot("/dev/sda2", identity, "Before repair", run=run)
        restore_snapshot(
            "/dev/sda2",
            identity,
            "11111111-1111-4111-8111-111111111111",
            True,
            run=run,
        )
        self.assertEqual(
            calls[0],
            ["pkexec", HELPER, "create-snapshot", "/dev/sda2", identity, "Before repair"],
        )
        self.assertEqual(calls[1][-1], "true")
