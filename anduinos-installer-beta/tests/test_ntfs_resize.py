import subprocess
import unittest

from installer_core.ntfs_resize import (
    GIB,
    MIB,
    NTFS_PROBE,
    NTFS_RESIZE,
    NtfsResizeBlockReason,
    NtfsResizeInspection,
    inspect_ntfs_resize,
)


def completed(command, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class NtfsResizeInspectionTests(unittest.TestCase):
    def test_scheduled_disk_check_is_recognized_at_every_read_only_stage(self):
        for stage in ("--check", "--info", "--size"):
            for stream in ("stdout", "stderr"):
                with self.subTest(stage=stage, stream=stream):
                    calls = []

                    def run(command, **kwargs):
                        calls.append(command)
                        self.assertNotIn("--force", command)
                        self.assertNotIn("-f", command)
                        if command[0] == NTFS_RESIZE:
                            self.assertIn("--no-action", command)
                        if stage in command:
                            return completed(command, returncode=1, **{stream:
                                "ERROR: Volume is scheduled for check.\n"
                                "Run chkdsk /f and please try again, or see option -f.\n"})
                        if "--info" in command:
                            return completed(command, "You might resize at 10737418240 bytes.\n")
                        return completed(command)

                    result = inspect_ntfs_resize(
                        "/dev/sda3", 64 * GIB, target_size_bytes=32 * GIB, run=run,
                    )
                    self.assertFalse(result.safe)
                    self.assertIs(result.block_reason, NtfsResizeBlockReason.CHECK_REQUIRED)
                    self.assertIn(stage, calls[-1])
                    self.assertEqual(result, NtfsResizeInspection.from_json(result.to_json()))

    def test_unrelated_range_failure_does_not_claim_windows_check_is_required(self):
        def run(command, **_kwargs):
            if "--info" in command:
                return completed(command, stderr="Device read failed", returncode=1)
            return completed(command)

        result = inspect_ntfs_resize("/dev/sda3", 64 * GIB, run=run)
        self.assertIs(result.block_reason, NtfsResizeBlockReason.RANGE_UNAVAILABLE)
        self.assertEqual(result.message, "Device read failed")

    def test_healthy_volume_reports_conservative_aligned_range(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(tuple(command))
            if command[:2] == [NTFS_PROBE, "--readwrite"]:
                return completed(command)
            if "--check" in command:
                return completed(command)
            if "--info" in command:
                return completed(
                    command,
                    "You might resize at 10737418240 bytes or 10738 MB.\n",
                )
            return completed(command, "Every sanity check passed.\n")

        inspection = inspect_ntfs_resize(
            "/dev/nvme0n1p3",
            64 * GIB,
            target_size_bytes=32 * GIB,
            run=run,
        )

        self.assertTrue(inspection.safe)
        self.assertEqual(inspection.minimum_size_bytes, 25 * GIB // 2)
        self.assertEqual(inspection.maximum_size_bytes, 64 * GIB - MIB)
        self.assertEqual(inspection.target_size_bytes, 32 * GIB)
        self.assertTrue(all("--force" not in command for command in calls))
        self.assertTrue(all("-f" not in command for command in calls))

    def test_hibernation_and_unclean_shutdown_are_hard_blocks(self):
        for returncode, reason in (
            (14, NtfsResizeBlockReason.HIBERNATED),
            (15, NtfsResizeBlockReason.UNCLEAN),
        ):
            with self.subTest(returncode=returncode):
                calls = []

                def run(command, **_kwargs):
                    calls.append(tuple(command))
                    return completed(command, returncode=returncode)

                inspection = inspect_ntfs_resize(
                    "/dev/sda3", 128 * GIB, run=run
                )
                self.assertFalse(inspection.safe)
                self.assertIs(inspection.block_reason, reason)
                self.assertEqual(len(calls), 1)

    def test_bitlocker_and_mounted_volumes_run_no_ntfs_tools(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("No command may run")

        bitlocker = inspect_ntfs_resize(
            "/dev/sda3",
            128 * GIB,
            filesystem="BitLocker",
            run=forbidden,
        )
        mounted = inspect_ntfs_resize(
            "/dev/sda3",
            128 * GIB,
            mounted=True,
            run=forbidden,
        )
        self.assertIs(bitlocker.block_reason, NtfsResizeBlockReason.BITLOCKER)
        self.assertIs(mounted.block_reason, NtfsResizeBlockReason.MOUNTED)

    def test_target_that_relocates_mft_is_refused(self):
        def run(command, **_kwargs):
            if command[:2] == [NTFS_PROBE, "--readwrite"]:
                return completed(command)
            if "--check" in command:
                return completed(command)
            if "--info" in command:
                return completed(
                    command,
                    "You might resize at 8589934592 bytes.\n",
                )
            return completed(
                command,
                "Relocate record       0:0x80:00000001 --> 0x00100000\n",
            )

        inspection = inspect_ntfs_resize(
            "/dev/sda3",
            128 * GIB,
            target_size_bytes=64 * GIB,
            run=run,
        )
        self.assertIs(
            inspection.block_reason,
            NtfsResizeBlockReason.MFT_RELOCATION_UNSAFE,
        )

    def test_json_contract_round_trips_and_rejects_unknown_fields(self):
        inspection = NtfsResizeInspection(
            device="/dev/sda3",
            filesystem="ntfs",
            current_size_bytes=64 * GIB,
            minimum_size_bytes=16 * GIB,
            maximum_size_bytes=64 * GIB - MIB,
            block_reason=NtfsResizeBlockReason.NONE,
            message="safe",
            probe_exit_code=0,
        )
        self.assertEqual(
            NtfsResizeInspection.from_json(inspection.to_json()), inspection
        )
        value = inspection.to_dict()
        value["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            NtfsResizeInspection.from_dict(value)


if __name__ == "__main__":
    unittest.main()
