import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fakes import FakeRunner
from guided_test_evidence_cli import (
    _post_install_esp,
    capture,
    require_safe_output,
    verify_windows_fixture_boot_entry,
)
from test_guided_evidence import NVRAM_BEFORE
from test_guided_executor import post_write_inventory
from test_guided_storage_planning import build_execution
from installer_core.esp import (
    EspReuseInspection,
    EspTreeEntry,
    NvramInspection,
)
from installer_core.guided_evidence import (
    GuidedVmEvidence,
    PreservedPartitionDigest,
)
from installer_core.storage_preservation import (
    capture_guided_preservation_snapshot,
)


class GuidedTestEvidenceCliTests(unittest.TestCase):
    def test_capture_writes_a_private_strict_manifest(self):
        plan, inventory, execution = build_execution()
        esp = inventory.disks[0].partitions[0]
        preserved_file = EspTreeEntry(
            "EFI/Microsoft/Boot/bootmgfw.efi",
            "file",
            7,
            "b" * 64,
        )
        inspection = EspReuseInspection(
            partuuid=esp.identity.partuuid,
            filesystem_uuid=esp.filesystem_uuid,
            healthy=True,
            free_bytes=128 * 1024 * 1024,
            preserved_entries=(preserved_file,),
        )
        snapshot = capture_guided_preservation_snapshot(
            plan, inventory, execution.write_set
        )
        digests = tuple(
            PreservedPartitionDigest(item.partuuid, "a" * 64)
            for item in snapshot.partitions
            if item.partuuid != esp.identity.partuuid
        )
        runner = FakeRunner()
        runner.outputs[("efibootmgr", "--verbose")] = (
            NVRAM_BEFORE,
            "",
            0,
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "before.json"
            with (
                patch(
                    "guided_test_evidence_cli.probe_storage_inventory",
                    return_value=inventory,
                ),
                patch(
                    "guided_test_evidence_cli.resolve_guided_esp_partition",
                    return_value=(esp, True),
                ),
                patch(
                    "guided_test_evidence_cli.inspect_esp_for_reuse",
                    return_value=inspection,
                ),
                patch(
                    "guided_test_evidence_cli.inspect_nvram",
                    return_value=NvramInspection(True),
                ),
                patch(
                    "guided_test_evidence_cli."
                    "build_guided_coexistence_execution_plan",
                    return_value=execution,
                ),
                patch(
                    "guided_test_evidence_cli.capture_partition_digests",
                    return_value=digests,
                ),
            ):
                capture(plan, output, runner)

            evidence = GuidedVmEvidence.from_dict(
                json.loads(output.read_text())
            )
            self.assertEqual(evidence.esp_entries, (preserved_file,))
            self.assertEqual(
                stat.S_IMODE(output.stat().st_mode),
                0o600,
            )

    def test_post_install_esp_resolves_reused_and_new_targets(self):
        reused_plan, reused_inventory, _execution = build_execution()
        reused = _post_install_esp(
            reused_plan,
            SimpleNamespace(reused_esp_partuuid="part-1"),
            reused_inventory.disks[0].partitions,
        )
        self.assertEqual(reused.identity.partuuid, "part-1")

        new_plan, new_inventory, _execution = build_execution(
            reuse_esp=False
        )
        after = post_write_inventory(new_plan, new_inventory)
        created = _post_install_esp(
            new_plan,
            SimpleNamespace(reused_esp_partuuid=""),
            after.disks[0].partitions,
        )
        self.assertTrue(created.is_efi_system_partition)

        with self.assertRaisesRegex(RuntimeError, "disappeared"):
            _post_install_esp(
                reused_plan,
                SimpleNamespace(reused_esp_partuuid="missing"),
                reused_inventory.disks[0].partitions,
            )

    def test_evidence_output_rejects_kernel_and_device_trees(self):
        for path in (
            Path("/dev/vda"),
            Path("/proc/evidence.json"),
            Path("/sys/evidence.json"),
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(RuntimeError, "cannot target"):
                    require_safe_output(path)
        require_safe_output(Path("/tmp/guided-evidence.json"))

    def test_windows_boot_entry_must_match_the_fixture_esp(self):
        plan, inventory, _execution = build_execution()
        verify_windows_fixture_boot_entry(plan, inventory, NVRAM_BEFORE)
        with self.assertRaisesRegex(RuntimeError, "Paired fixture VARS"):
            verify_windows_fixture_boot_entry(
                plan,
                inventory,
                NVRAM_BEFORE.replace("part-1", "wrong-partuuid"),
            )


if __name__ == "__main__":
    unittest.main()
