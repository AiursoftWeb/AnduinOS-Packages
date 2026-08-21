import json
import os
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import patch

from installer_core.probe import ProbeError
from installer_core.storage_inventory import (
    EFI_SYSTEM_PARTITION_GUID,
    StaleStorageInventoryError,
    _disk_topology_digest,
    _parse_parted_machine,
    bind_disk_topology,
    probe_storage_inventory,
    verify_disk_topology,
)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class StorageInventoryTests(unittest.TestCase):
    def lsblk_payload(self):
        return {
            "blockdevices": [
                {
                    "path": "/dev/nvme0n1",
                    "size": 100_000_000_000,
                    "model": "Test SSD",
                    "serial": "DISK-1",
                    "wwn": None,
                    "type": "disk",
                    "rm": False,
                    "maj:min": "259:0",
                    "log-sec": 512,
                    "pttype": "gpt",
                    "ptuuid": "DISK-GUID",
                    "children": [
                        {
                            "path": "/dev/nvme0n1p1",
                            "size": 1_073_741_824,
                            "type": "part",
                            "partn": 1,
                            "start": 2048,
                            "partuuid": "ESP-PARTUUID",
                            "parttype": EFI_SYSTEM_PARTITION_GUID.upper(),
                            "fstype": "vfat",
                            "uuid": "ESP-UUID",
                            "label": "EFI",
                            "mountpoints": [None],
                        },
                        {
                            "path": "/dev/nvme0n1p2",
                            "size": 60_000_000_000,
                            "type": "part",
                            "partn": 2,
                            "start": 2_099_200,
                            "partuuid": "WINDOWS-PARTUUID",
                            "parttype": "microsoft-basic-data",
                            "fstype": "ntfs",
                            "uuid": "WINDOWS-UUID",
                            "label": "Windows",
                            "mountpoints": [],
                        },
                    ],
                }
            ]
        }

    def parted_output(self):
        return """BYT;
/dev/nvme0n1:100000000000B:nvme:512:4096:gpt:Test SSD:;
1:1048576B:1074790399B:1073741824B:fat32:EFI\\: System:boot, esp;
2:1074790400B:61074790399B:60000000000B:ntfs:Windows:msftdata;
:61074790400B:99999999487B:38925209088B:free;
"""

    def probe(self, *, parted=None):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[0] == "lsblk":
                return completed(json.dumps(self.lsblk_payload()))
            return parted or completed(self.parted_output())

        inventory = probe_storage_inventory(run=run)
        return inventory, calls

    def test_probes_partitions_esp_and_free_extents(self):
        inventory, calls = self.probe()
        self.assertEqual(len(inventory.disks), 1)
        disk = inventory.disks[0]
        self.assertEqual(disk.identity.stable_id, "serial:DISK-1")
        self.assertEqual(disk.partition_table, "gpt")
        self.assertEqual(disk.partition_table_uuid, "disk-guid")
        self.assertEqual(len(disk.partitions), 2)
        esp = disk.partitions[0]
        self.assertEqual(esp.identity.start_bytes, 1_048_576)
        self.assertEqual(esp.identity.size_bytes, 1_073_741_824)
        self.assertEqual(esp.flags, ("boot", "esp"))
        self.assertTrue(esp.is_efi_system_partition)
        self.assertTrue(esp.is_efi_filesystem_candidate)
        self.assertEqual(len(disk.free_extents), 1)
        self.assertEqual(disk.free_extents[0].start_bytes, 61_074_790_400)
        self.assertEqual(disk.free_extents[0].size_bytes, 38_925_209_088)
        self.assertEqual(calls[0][0], "lsblk")
        self.assertIn("--tree", calls[0])
        self.assertEqual(calls[1][0], "parted")
        self.assertIn("print", calls[1])
        self.assertIn("free", calls[1])

    def test_parted_runner_is_separate_from_unprivileged_lsblk_runner(self):
        lsblk_calls = []
        parted_calls = []

        def run(command, **_kwargs):
            lsblk_calls.append(command)
            return completed(json.dumps(self.lsblk_payload()))

        def parted_run(command, **_kwargs):
            parted_calls.append(command)
            return completed(self.parted_output())

        inventory = probe_storage_inventory(
            run=run,
            parted_run=parted_run,
        )

        self.assertEqual(len(inventory.disks[0].partitions), 2)
        self.assertEqual(len(lsblk_calls), 1)
        self.assertEqual(lsblk_calls[0][0], "lsblk")
        self.assertEqual(len(parted_calls), 1)
        self.assertEqual(parted_calls[0][0], "parted")

    def test_storage_digest_is_independent_from_process_locale(self):
        environments = []

        def run(command, **kwargs):
            environment = kwargs.get("env", os.environ)
            environments.append(
                (
                    command[0],
                    environment.get("LC_ALL"),
                    environment.get("LANGUAGE"),
                )
            )
            if command[0] == "lsblk":
                return completed(json.dumps(self.lsblk_payload()))
            output = self.parted_output()
            if environment.get("LC_ALL") != "C":
                output = output.replace("boot, esp", "启动, esp")
                output = output.replace("msftdata", "微软数据")
            return completed(output)

        with patch.dict(
            os.environ,
            {"LC_ALL": "zh_CN.UTF-8", "LANGUAGE": "zh_CN"},
        ):
            localized = probe_storage_inventory(run=run)
        with patch.dict(
            os.environ,
            {"LC_ALL": "C", "LANGUAGE": "C"},
        ):
            canonical = probe_storage_inventory(run=run)

        self.assertEqual(
            localized.disks[0].topology_digest,
            canonical.disks[0].topology_digest,
        )
        self.assertEqual(
            localized.disks[0].partitions[0].flags,
            ("boot", "esp"),
        )
        self.assertTrue(environments)
        self.assertTrue(
            all(
                lc_all == "C" and language == "C"
                for _command, lc_all, language in environments
            )
        )

    def test_parted_parser_unescapes_labels_without_changing_fields(self):
        geometry = _parse_parted_machine(self.parted_output())
        self.assertEqual(len(geometry.partitions), 2)
        self.assertEqual(geometry.partitions[0].number, 1)
        self.assertEqual(geometry.partitions[0].flags, ("boot", "esp"))
        self.assertEqual(geometry.free_extents[0], (61_074_790_400, 38_925_209_088))

    def test_parted_parser_treats_numbered_free_rows_as_free_space(self):
        geometry = _parse_parted_machine(
            """BYT;
/dev/nvme0n1:1024209543168B:nvme:4096:4096:gpt:Test SSD:;
1:24576B:1048575B:1024000B:free;
1:1048576B:537919487B:536870912B:fat32:EFI System:boot, esp;
2:537919488B:1024209190911B:1023671271424B:ext4::;
1:1024209190912B:1024209522687B:331776B:free;
"""
        )
        self.assertEqual(
            [(item.number, item.start_bytes, item.size_bytes) for item in geometry.partitions],
            [
                (1, 1_048_576, 536_870_912),
                (2, 537_919_488, 1_023_671_271_424),
            ],
        )
        self.assertEqual(
            geometry.free_extents,
            ((24_576, 1_024_000), (1_024_209_190_912, 331_776)),
        )

    def test_disk_without_readable_table_remains_erasable_but_has_no_free_space(self):
        inventory, _calls = self.probe(
            parted=completed("", "unrecognised disk label", 1)
        )
        disk = inventory.disks[0]
        self.assertEqual(disk.free_extents, ())
        self.assertEqual(disk.geometry_probe_error, "unrecognised disk label")
        self.assertEqual(len(disk.partitions), 2)
        self.assertEqual(disk.partitions[0].identity.start_bytes, 2048 * 512)

    def test_binding_rejects_changed_topology(self):
        inventory, _calls = self.probe()
        binding = bind_disk_topology(inventory, "serial:DISK-1")
        self.assertIs(verify_disk_topology(binding, inventory), inventory.disks[0])

        disk = inventory.disks[0]
        changed_partitions = (
            replace(
                disk.partitions[0],
                filesystem_uuid="replacement-filesystem",
            ),
            *disk.partitions[1:],
        )
        changed_disk = replace(
            disk,
            partitions=changed_partitions,
            topology_digest=_disk_topology_digest(
                disk.identity,
                disk.partition_table,
                disk.partition_table_uuid,
                changed_partitions,
                disk.free_extents,
                disk.geometry_probe_error,
            ),
        )
        changed_inventory = replace(inventory, disks=(changed_disk,))
        with self.assertRaisesRegex(
            StaleStorageInventoryError, "topology changed"
        ):
            verify_disk_topology(binding, changed_inventory)

    def test_binding_rejects_missing_or_resized_disk(self):
        inventory, _calls = self.probe()
        binding = bind_disk_topology(inventory, "serial:DISK-1")
        with self.assertRaisesRegex(
            StaleStorageInventoryError, "no longer present"
        ):
            verify_disk_topology(binding, replace(inventory, disks=()))

        disk = inventory.disks[0]
        resized = replace(
            disk,
            identity=replace(
                disk.identity,
                expected_size_bytes=disk.identity.expected_size_bytes + 1,
            ),
        )
        with self.assertRaisesRegex(StaleStorageInventoryError, "size changed"):
            verify_disk_topology(binding, replace(inventory, disks=(resized,)))

    def test_invalid_lsblk_json_is_fatal(self):
        with self.assertRaisesRegex(ProbeError, "invalid storage JSON"):
            probe_storage_inventory(
                run=lambda *_args, **_kwargs: completed("not json")
            )

    def test_display_paths_do_not_authorize_topology(self):
        inventory, _calls = self.probe()
        disk = inventory.disks[0]
        moved_partitions = tuple(
            replace(
                item,
                identity=replace(
                    item.identity,
                    path=item.identity.path.replace("nvme0n1", "nvme1n1"),
                ),
            )
            for item in disk.partitions
        )
        self.assertEqual(
            disk.topology_digest,
            _disk_topology_digest(
                disk.identity,
                disk.partition_table,
                disk.partition_table_uuid,
                moved_partitions,
                disk.free_extents,
                disk.geometry_probe_error,
            ),
        )

    def test_mismatched_geometry_hides_free_extents(self):
        payload = self.lsblk_payload()
        payload["blockdevices"][0]["children"].append(
            {
                "path": "/dev/nvme0n1p3",
                "size": 1_000_000,
                "type": "part",
                "partn": 3,
                "start": 150_000_000,
                "partuuid": "STALE-PARTITION",
                "parttype": "linux-filesystem",
                "fstype": "ext4",
                "uuid": "STALE-FS",
                "mountpoints": [],
            }
        )

        def run(command, **_kwargs):
            if command[0] == "lsblk":
                return completed(json.dumps(payload))
            return completed(self.parted_output())

        disk = probe_storage_inventory(run=run).disks[0]
        self.assertEqual(disk.free_extents, ())
        self.assertIn("partition sets differ", disk.geometry_probe_error)

    def test_records_nested_mappings_that_guided_mode_cannot_modify(self):
        payload = self.lsblk_payload()
        payload["blockdevices"][0]["children"][1]["children"] = [
            {
                "path": "/dev/mapper/secured-windows",
                "size": 60_000_000_000,
                "type": "crypt",
                "mountpoints": [],
            }
        ]

        def run(command, **_kwargs):
            if command[0] == "lsblk":
                return completed(json.dumps(payload))
            return completed(self.parted_output())

        disk = probe_storage_inventory(run=run).disks[0]
        self.assertEqual(disk.unsupported_descendant_types, ("crypt",))
        self.assertTrue(disk.free_extents)

    def test_geometry_error_text_is_not_an_authorization_identity(self):
        inventory, _calls = self.probe(
            parted=completed("", "localized error one", 1)
        )
        disk = inventory.disks[0]
        changed_error = replace(disk, geometry_probe_error="localized error two")
        self.assertEqual(
            disk.topology_digest,
            _disk_topology_digest(
                changed_error.identity,
                changed_error.partition_table,
                changed_error.partition_table_uuid,
                changed_error.partitions,
                changed_error.free_extents,
                changed_error.geometry_probe_error,
            ),
        )


if __name__ == "__main__":
    unittest.main()
