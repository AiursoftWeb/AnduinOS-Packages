import io
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from helpers import TEST_INVENTORY_DIGEST, TEST_TOPOLOGY_DIGEST
from frontend import (
    DevelopmentExecutorClient,
    ExecutorClient,
    FrontendPlanError,
    StorageStrategy,
    _run_privileged_parted,
    apply_storage_strategy,
    bind_storage_target,
    clear_storage_target,
    create_install_plan,
    guided_storage_enabled,
)
from installer_core.model import (
    Architecture,
    DiskIdentity,
    Filesystem,
    Firmware,
    InstallMode,
    SecureBoot,
)
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import DiskInventory, StorageInventory
from installer_core.storage_ui import (
    build_guided_storage_preview,
    build_storage_workflow,
    recommended_guided_selection,
)
from test_coexistence import windows_disk


def inventory_for(disk):
    return StorageInventory(
        (
            DiskInventory(
                identity=disk,
                partition_table="gpt",
                partition_table_uuid="test-table",
                partitions=(),
                free_extents=(),
                topology_digest=TEST_TOPOLOGY_DIGEST,
            ),
        ),
        TEST_INVENTORY_DIGEST,
    )


def state():
    return {
        "lang": "en_US",
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


def guided_state():
    disk = windows_disk(free_gib=24)
    inventory = StorageInventory((disk,), "e" * 64)
    platform = PlatformProbe(
        Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
    )
    workflow = build_storage_workflow(inventory, platform)
    preview = build_guided_storage_preview(
        workflow,
        recommended_guided_selection(workflow.disks[0], Filesystem.BTRFS),
    )
    values = state()
    values.update(
        {
            "disk": disk.identity.path,
            "disk_size_bytes": disk.identity.expected_size_bytes,
            "disk_stable_id": disk.identity.stable_id,
            "disk_model": disk.identity.model,
            "storage_mode": InstallMode.GUIDED_COEXISTENCE.value,
            "guided_storage_preview_model": preview,
        }
    )
    return values, disk, inventory, platform


class FrontendPlanTests(unittest.TestCase):
    def test_storage_geometry_uses_only_the_polkit_probe_when_unprivileged(self):
        command = [
            "parted",
            "--machine",
            "--script",
            "/dev/nvme0n1",
            "unit",
            "B",
            "print",
            "free",
        ]
        with (
            patch("frontend.os.geteuid", return_value=1000),
            patch("frontend.subprocess.run") as run,
        ):
            _run_privileged_parted(command, capture_output=True, text=True)
        run.assert_called_once_with(
            [
                "pkexec",
                "/usr/bin/anduinos-installer-storage-probe",
                "/dev/nvme0n1",
            ],
            capture_output=True,
            text=True,
        )

    def test_storage_geometry_rejects_any_mutating_privileged_command(self):
        with patch("frontend.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "non-read-only"):
                _run_privileged_parted(
                    [
                        "parted",
                        "--script",
                        "/dev/nvme0n1",
                        "mklabel",
                        "gpt",
                    ]
                )
        run.assert_not_called()

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
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory_for(disk),
            ),
            patch("frontend.probe_platform", return_value=platform),
        ):
            return create_install_plan(values)

    def make_guided_plan(self):
        values, _disk, inventory, platform = guided_state()
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory,
            ),
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
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory_for(disk),
            ),
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
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory_for(disk),
            ),
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
        self.assertIn("Firmware mode: UEFI", simulated)
        self.assertIn("Secure Boot: enabled", simulated)
        self.assertIn("Selected target disk: /dev/sda", simulated)
        self.assertIn("Other disks and EFI System Partitions", simulated)
        self.assertIn("[refresh-package-indexes]", simulated)
        self.assertIn("[upgrade-system]", simulated)
        self.assertIn("Target filesystem: btrfs", simulated)
        self.assertIn("[ensure-timeback-machine]", simulated)
        self.assertIn("retain the installation-media payload", simulated)
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
        values["filesystem"] = Filesystem.EXT4.value
        disk = DiskIdentity(
            "/dev/sda", "serial:test", 64 * 1024**3, "Test", "test"
        )
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory_for(disk),
            ),
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
        self.assertNotIn("[ensure-timeback-machine]", simulated)
        self.assertIn("Target filesystem: ext4", simulated)
        self.assertIn("remove the live payload", simulated)

    def test_rejects_disk_that_changed_after_selection(self):
        values = state()
        replacement = DiskIdentity(
            "/dev/sda", "serial:replacement", 64 * 1024**3
        )
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory_for(replacement),
            ),
        ):
            with self.assertRaisesRegex(FrontendPlanError, "changed"):
                create_install_plan(values)
        self.assertEqual(values["password"], "")

    def test_device_path_is_only_a_display_hint(self):
        values = state()
        moved = DiskIdentity(
            "/dev/vda", "serial:test", 64 * 1024**3, "Test", "test"
        )
        platform = PlatformProbe(
            Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED
        )
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch(
                "frontend.probe_storage_inventory",
                return_value=inventory_for(moved),
            ),
            patch("frontend.probe_platform", return_value=platform),
        ):
            plan = create_install_plan(values)
        self.assertEqual(plan.storage.disk.path, "/dev/vda")
        self.assertNotIn("/dev/", json.dumps(plan.storage.graph.to_dict()))

    def test_guided_storage_is_always_enabled_in_beta(self):
        self.assertTrue(guided_storage_enabled())

    def test_target_change_clears_topology_dependent_storage_choices(self):
        values, _disk, inventory, platform = guided_state()
        values["storage_strategy"] = (
            StorageStrategy.ADVANCED_COEXISTENCE.value
        )
        values["guided_extent_id"] = "stale-extent"
        values["guided_esp_partuuid"] = "stale-esp"
        values["disk_topology_digest"] = "0" * 64
        choice = build_storage_workflow(inventory, platform).disks[0]

        self.assertTrue(bind_storage_target(values, choice))
        self.assertEqual(values["storage_strategy"], "")
        self.assertEqual(values["guided_extent_id"], "")
        self.assertEqual(values["guided_esp_partuuid"], "")
        self.assertEqual(
            values["disk_topology_digest"], choice.disk.topology_digest
        )
        self.assertTrue(values["disk_erase_available"])

    def test_same_target_preserves_strategy_until_topology_changes(self):
        values, _disk, inventory, platform = guided_state()
        choice = build_storage_workflow(inventory, platform).disks[0]
        bind_storage_target(values, choice)
        apply_storage_strategy(
            values, StorageStrategy.ADVANCED_COEXISTENCE
        )
        self.assertFalse(bind_storage_target(values, choice))
        self.assertEqual(
            values["storage_strategy"],
            StorageStrategy.ADVANCED_COEXISTENCE.value,
        )

        changed = replace(
            choice,
            disk=replace(choice.disk, topology_digest="1" * 64),
        )
        self.assertTrue(bind_storage_target(values, changed))
        self.assertEqual(values["storage_strategy"], "")

    def test_storage_strategy_maps_to_mode_and_filesystem(self):
        values = state()
        values["guided_extent_id"] = "old"
        apply_storage_strategy(values, StorageStrategy.ERASE_EXT4)
        self.assertEqual(values["storage_mode"], "erase-disk")
        self.assertEqual(values["filesystem"], "ext4")
        self.assertEqual(values["guided_extent_id"], "")

        apply_storage_strategy(
            values, StorageStrategy.ADVANCED_COEXISTENCE
        )
        self.assertEqual(values["storage_mode"], "guided-coexistence")
        self.assertEqual(values["filesystem"], "btrfs")

        clear_storage_target(values)
        self.assertEqual(values["disk_stable_id"], "")
        self.assertEqual(values["storage_strategy"], "")
        self.assertFalse(values["disk_erase_available"])

    def test_guided_state_builds_from_a_fresh_topology_probe(self):
        values, _disk, inventory, platform = guided_state()
        old_preview = values["guided_storage_preview_model"]
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch("frontend.probe_storage_inventory", return_value=inventory),
            patch("frontend.probe_platform", return_value=platform),
        ):
            plan = create_install_plan(values)
        self.assertIs(plan.storage.mode, InstallMode.GUIDED_COEXISTENCE)
        self.assertEqual(plan.storage.graph, old_preview.graph)
        self.assertIsNot(values["guided_storage_preview_model"], old_preview)
        self.assertEqual(values["password"], "")
        self.assertEqual(values["password_confirmation"], "")

    def test_guided_state_rejects_a_changed_free_extent(self):
        values, disk, _inventory, platform = guided_state()
        changed = replace(disk, free_extents=())
        inventory = StorageInventory((changed,), "f" * 64)
        with (
            patch("frontend.hash_password", return_value="$6$salt$hash"),
            patch("frontend.probe_storage_inventory", return_value=inventory),
            patch("frontend.probe_platform", return_value=platform),
        ):
            with self.assertRaisesRegex(
                FrontendPlanError, "unallocated space or EFI partition changed"
            ):
                create_install_plan(values)

    def test_executor_client_starts_public_helper_for_guided_plan(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(
                    '{"event":"complete","error":""}\n'
                )
                self.stderr = io.StringIO("")

            def wait(self):
                return 0

        process = FakeProcess()
        process.stdin.close = lambda: None
        with patch("frontend.subprocess.Popen", return_value=process) as popen:
            succeeded, error = ExecutorClient("/test/executor").run(
                self.make_guided_plan(),
                lambda _message: None,
                lambda *_args: None,
            )
        self.assertTrue(succeeded)
        self.assertEqual(error, "")
        popen.assert_called_once()
