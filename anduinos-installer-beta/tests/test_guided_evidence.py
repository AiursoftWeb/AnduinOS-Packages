import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_guided_storage_graph import guided_plan
from test_guided_storage_planning import build_execution
from installer_core.destructive_test import (
    GUIDED_TEST_ENVIRONMENT,
    require_disposable_guided_vm,
)
from installer_core.esp import EspTreeEntry
from installer_core.guided_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    GuidedVmEvidence,
    NvramEvidence,
    PreservedPartitionDigest,
    capture_nvram_evidence,
    hash_partition,
    plan_sha256,
    validate_evidence,
    verify_nvram_evidence,
)
from installer_core.storage_preservation import (
    capture_guided_preservation_snapshot,
)


NVRAM_BEFORE = (
    "BootOrder: 0001,0002\n"
    "Boot0001* Windows Boot Manager "
    "HD(1,GPT,part-1,0x800,0x100000)/"
    "File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"
    "Boot0002* UEFI Shell VenHw(test)\n"
)


def evidence_fixture():
    plan, inventory, execution = build_execution()
    snapshot = capture_guided_preservation_snapshot(
        plan, inventory, execution.write_set
    )
    return plan, GuidedVmEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        plan_sha256=plan_sha256(plan),
        preservation=snapshot,
        partition_digests=tuple(
            PreservedPartitionDigest(item.partuuid, "a" * 64)
            for item in snapshot.partitions
            if item.partuuid != "part-1"
        ),
        reused_esp_partuuid="part-1",
        esp_entries=(
            EspTreeEntry(
                "EFI/Microsoft/Boot/bootmgfw.efi",
                "file",
                7,
                "b" * 64,
            ),
        ),
        nvram=capture_nvram_evidence(NVRAM_BEFORE),
    )


class GuidedEvidenceTests(unittest.TestCase):
    def test_strict_json_round_trip_preserves_tuple_fields(self):
        _plan, evidence = evidence_fixture()
        encoded = json.loads(json.dumps(evidence.to_dict()))
        decoded = GuidedVmEvidence.from_dict(encoded)
        self.assertEqual(decoded, evidence)
        self.assertIsInstance(decoded.preservation.partitions[0].flags, tuple)

        encoded["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            GuidedVmEvidence.from_dict(encoded)

    def test_nested_evidence_fields_are_strict(self):
        _plan, evidence = evidence_fixture()
        invalid_digest = json.loads(json.dumps(evidence.to_dict()))
        invalid_digest["partition_digests"][0]["sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "digest value"):
            GuidedVmEvidence.from_dict(invalid_digest)

        invalid_nvram = json.loads(json.dumps(evidence.to_dict()))
        invalid_nvram["nvram"]["entries"][0] = ["0001"]
        with self.assertRaisesRegex(ValueError, "NVRAM evidence entries"):
            GuidedVmEvidence.from_dict(invalid_nvram)

        invalid_esp = json.loads(json.dumps(evidence.to_dict()))
        invalid_esp["esp_entries"][0]["relative_path"] = "../escape"
        with self.assertRaisesRegex(ValueError, "shared ESP evidence"):
            GuidedVmEvidence.from_dict(invalid_esp)

        missing_windows = json.loads(json.dumps(evidence.to_dict()))
        missing_windows["nvram"]["entries"][0][1] = (
            "uefi shell venhw(test)"
        )
        with self.assertRaisesRegex(ValueError, "Windows boot entry"):
            GuidedVmEvidence.from_dict(missing_windows)

    def test_every_non_shared_partition_requires_a_full_digest(self):
        _plan, evidence = evidence_fixture()
        validate_evidence(evidence)
        incomplete = replace(
            evidence,
            partition_digests=evidence.partition_digests[:-1],
        )
        with self.assertRaisesRegex(ValueError, "partition digests"):
            validate_evidence(incomplete)

    def test_partition_hasher_requires_the_exact_authorized_length(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partition.img"
            path.write_bytes(b"preserved-bytes")
            digest = hash_partition(path, len(b"preserved-bytes"))
            self.assertEqual(len(digest), 64)
            with self.assertRaisesRegex(RuntimeError, "larger"):
                hash_partition(path, len(b"preserved-bytes") - 1)
            with self.assertRaisesRegex(RuntimeError, "ended early"):
                hash_partition(path, len(b"preserved-bytes") + 1)

    def test_nvram_preserves_entries_and_relative_boot_order(self):
        expected = capture_nvram_evidence(NVRAM_BEFORE)
        after = (
            "BootOrder: 0007,0001,0002\n"
            + NVRAM_BEFORE.split("\n", 1)[1]
            + "Boot0007* AnduinOS "
            "HD(1,GPT,part-1,0x800,0x100000)/"
            "File(\\EFI\\AnduinOS\\shimx64.efi)\n"
        )
        verify_nvram_evidence(expected, after)

        with self.assertRaisesRegex(RuntimeError, "boot order"):
            verify_nvram_evidence(
                expected,
                after.replace(
                    "BootOrder: 0007,0001,0002",
                    "BootOrder: 0007,0002,0001",
                ),
            )
        with self.assertRaisesRegex(RuntimeError, "entry changed"):
            verify_nvram_evidence(
                expected,
                after.replace("bootmgfw.efi", "changed.efi"),
            )

    def test_evidence_gate_requires_root_qemu_and_vda(self):
        plan, _inventory = guided_plan()
        vda = replace(
            plan,
            storage=replace(
                plan.storage,
                disk=replace(plan.storage.disk, path="/dev/vda"),
            ),
        )
        environment = {GUIDED_TEST_ENVIRONMENT: "1"}
        require_disposable_guided_vm(
            vda,
            environment=environment,
            effective_uid=0,
            virtualization="qemu",
        )
        cases = (
            (plan, environment, 0, "qemu", "/dev/vda"),
            (vda, {}, 0, "qemu", "marker"),
            (vda, environment, 1000, "qemu", "root"),
            (vda, environment, 0, "none", "QEMU/KVM"),
        )
        for candidate, env, uid, virtualization, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    require_disposable_guided_vm(
                        candidate,
                        environment=env,
                        effective_uid=uid,
                        virtualization=virtualization,
                    )


if __name__ == "__main__":
    unittest.main()
