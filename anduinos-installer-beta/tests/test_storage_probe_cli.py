import contextlib
import io
import json
import subprocess
import unittest

from storage_probe_cli import main


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class StorageProbeCliTests(unittest.TestCase):
    def test_runs_only_fixed_read_only_commands_for_one_fixed_disk(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[0] == "/usr/bin/lsblk":
                return completed(
                    json.dumps(
                        {
                            "blockdevices": [
                                {
                                    "path": "/dev/nvme0n1",
                                    "type": "disk",
                                    "rm": False,
                                }
                            ]
                        }
                    )
                )
            return completed("BYT;\n", "", 0)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = main(
                ["/dev/nvme0n1"], run=run, geteuid=lambda: 0
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.getvalue(), "BYT;\n")
        self.assertEqual(calls[0][0], "/usr/bin/lsblk")
        self.assertEqual(
            calls[1],
            [
                "/usr/sbin/parted",
                "--machine",
                "--script",
                "/dev/nvme0n1",
                "unit",
                "B",
                "print",
                "free",
            ],
        )

    def test_rejects_unprivileged_or_non_whole_disk_requests(self):
        run_calls = []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(
                main(["/dev/nvme0n1"], run=run_calls.append, geteuid=lambda: 1000),
                2,
            )
            self.assertEqual(
                main(["/dev/nvme0n1p1"], run=run_calls.append, geteuid=lambda: 0),
                2,
            )
        self.assertEqual(run_calls, [])

    def test_rejects_a_removable_device_before_parted(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            return completed(
                json.dumps(
                    {
                        "blockdevices": [
                            {
                                "path": "/dev/sda",
                                "type": "disk",
                                "rm": True,
                            }
                        ]
                    }
                )
            )

        with contextlib.redirect_stderr(io.StringIO()):
            returncode = main(["/dev/sda"], run=run, geteuid=lambda: 0)
        self.assertEqual(returncode, 2)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
