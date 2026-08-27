"""Unprivileged GTK-to-executor boundary."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import replace
from enum import Enum

from installer_core.executor import describe_installation_pipeline
from installer_core.model import Filesystem, InstallMode, InstallPlan
from installer_core.passwords import hash_password
from installer_core.planning import build_plan
from installer_core.probe import PlatformProbe, probe_platform
from installer_core.storage_inventory import (
    StorageInventory,
    bind_disk_topology,
    probe_storage_inventory as _probe_storage_inventory,
)
from installer_core.storage_ui import (
    GuidedStoragePreview,
    StorageDiskChoice,
    build_guided_storage_preview,
    build_storage_workflow,
)
from installer_core.validation import validate_plan


class FrontendPlanError(RuntimeError):
    pass


_STORAGE_PROBE_HELPER = "/usr/bin/anduinos-installer-storage-probe"
_PARTED_READ_ONLY_SUFFIX = ("unit", "B", "print", "free")


def _run_privileged_parted(
    command: list[str], **kwargs: object
) -> subprocess.CompletedProcess[str]:
    """Run only the fixed read-only geometry probe across the root boundary."""

    if (
        len(command) != 8
        or command[:3] != ["parted", "--machine", "--script"]
        or tuple(command[4:]) != _PARTED_READ_ONLY_SUFFIX
    ):
        raise ValueError("Refusing a non-read-only privileged storage command")
    disk = command[3]
    if os.geteuid() == 0:
        return subprocess.run(command, **kwargs)
    return subprocess.run(
        ["pkexec", _STORAGE_PROBE_HELPER, disk],
        **kwargs,
    )


def probe_storage_inventory():
    """Probe storage while keeping the GTK process unprivileged.

    ``lsblk`` remains in the desktop process. Only exact, read-only partition
    geometry is delegated to the Polkit-authorized helper.
    """

    return _probe_storage_inventory(parted_run=_run_privileged_parted)


class StorageStrategy(str, Enum):
    ERASE_BTRFS = "erase-btrfs"
    ERASE_EXT4 = "erase-ext4"
    ADVANCED_COEXISTENCE = "advanced-coexistence"


def guided_storage_enabled() -> bool:
    """Coexistence is an unconditional beta capability."""

    return True


def clear_guided_storage_selection(state: dict[str, object]) -> None:
    state["guided_extent_id"] = ""
    state["guided_esp_partuuid"] = ""
    state["guided_storage_preview_model"] = None
    state["_guided_storage_workflow_model"] = None


def clear_storage_strategy(state: dict[str, object]) -> None:
    """Clear choices whose meaning depends on one exact target topology."""

    state["storage_strategy"] = ""
    state["storage_mode"] = InstallMode.ERASE_DISK.value
    state["filesystem"] = Filesystem.BTRFS.value
    clear_guided_storage_selection(state)


def clear_storage_target(state: dict[str, object]) -> None:
    for key in (
        "disk",
        "disk_size",
        "disk_model",
        "disk_stable_id",
        "disk_topology_digest",
    ):
        state[key] = ""
    state["disk_size_bytes"] = 0
    state["disk_windows_detected"] = False
    state["disk_bitlocker_detected"] = False
    state["disk_has_existing_partitions"] = False
    state["disk_erase_available"] = False
    clear_storage_strategy(state)


def bind_storage_target(
    state: dict[str, object],
    choice: StorageDiskChoice,
) -> bool:
    """Bind one disk and invalidate every choice from another topology."""

    disk = choice.disk.identity
    changed = (
        str(state.get("disk_stable_id") or "") != disk.stable_id
        or int(state.get("disk_size_bytes") or 0)
        != disk.expected_size_bytes
        or (
            bool(state.get("disk_topology_digest"))
            and state.get("disk_topology_digest")
            != choice.disk.topology_digest
        )
    )
    if changed:
        clear_storage_strategy(state)
    state["disk"] = disk.path
    state["disk_size"] = _human_size(disk.expected_size_bytes)
    state["disk_size_bytes"] = disk.expected_size_bytes
    state["disk_model"] = disk.model
    state["disk_stable_id"] = disk.stable_id
    state["disk_topology_digest"] = choice.disk.topology_digest
    state["disk_windows_detected"] = choice.coexistence.windows_detected
    state["disk_bitlocker_detected"] = choice.coexistence.bitlocker_detected
    state["disk_has_existing_partitions"] = bool(choice.disk.partitions)
    state["disk_erase_available"] = choice.erase_available
    return changed


def apply_storage_strategy(
    state: dict[str, object],
    strategy: StorageStrategy,
) -> None:
    if not isinstance(strategy, StorageStrategy):
        raise ValueError("Invalid storage strategy")
    changed = state.get("storage_strategy") != strategy.value
    if changed:
        clear_guided_storage_selection(state)
        state.pop("swap_size_mib", None)
    state["storage_strategy"] = strategy.value
    if strategy is StorageStrategy.ERASE_BTRFS:
        state["storage_mode"] = InstallMode.ERASE_DISK.value
        state["filesystem"] = Filesystem.BTRFS.value
    elif strategy is StorageStrategy.ERASE_EXT4:
        state["storage_mode"] = InstallMode.ERASE_DISK.value
        state["filesystem"] = Filesystem.EXT4.value
    else:
        state["storage_mode"] = InstallMode.GUIDED_COEXISTENCE.value
        if changed or state.get("filesystem") not in {
            Filesystem.BTRFS.value,
            Filesystem.EXT4.value,
        }:
            state["filesystem"] = Filesystem.BTRFS.value


def _human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def clear_plaintext_passwords(state: dict[str, object]) -> None:
    """Erase account secrets from both shared state and the visible entries."""

    state["password"] = ""
    state["password_confirmation"] = ""
    clear_ui = state.pop("_clear_password_ui", None)
    if callable(clear_ui):
        clear_ui()


def create_install_plan(
    state: dict[str, object],
    *,
    inventory: StorageInventory | None = None,
    platform: PlatformProbe | None = None,
    clear_passwords: bool = True,
) -> InstallPlan:
    try:
        storage_mode = InstallMode(
            str(state.get("storage_mode", InstallMode.ERASE_DISK.value))
        )
    except ValueError as error:
        if clear_passwords:
            clear_plaintext_passwords(state)
        raise FrontendPlanError("Unsupported storage mode") from error
    if storage_mode not in {
        InstallMode.ERASE_DISK,
        InstallMode.GUIDED_COEXISTENCE,
    }:
        if clear_passwords:
            clear_plaintext_passwords(state)
        raise FrontendPlanError("Unsupported storage mode")

    password = str(state.get("password") or "")
    confirmation = str(state.get("password_confirmation") or "")
    try:
        if not password or not confirmation:
            raise FrontendPlanError("A password is required")
        if password != confirmation:
            raise FrontendPlanError("The two passwords do not match")
        password_hash = hash_password(password)
    finally:
        # One-shot callers clear immediately. The GTK confirmation flow keeps
        # the entries only until the user accepts the already validated plan,
        # so cancelling the confirmation does not force password re-entry.
        if clear_passwords:
            clear_plaintext_passwords(state)

    selected_path = str(state.get("disk") or "")
    selected_id = str(state.get("disk_stable_id") or "")
    selected_size = int(state.get("disk_size_bytes") or 0)
    if inventory is None:
        inventory = probe_storage_inventory()
    selected = next(
        (
            item
            for item in inventory.disks
            if item.identity.stable_id == selected_id
            and item.identity.expected_size_bytes == selected_size
        ),
        None,
    )
    if selected is None:
        raise FrontendPlanError(
            "The selected disk changed or disappeared; select it again"
        )
    selected_topology = str(state.get("disk_topology_digest") or "")
    if (
        selected_topology
        and selected.topology_digest != selected_topology
    ):
        raise FrontendPlanError(
            "The selected disk topology changed; select it again"
        )
    # The path selected by GTK is a display hint only. Stable identity and
    # topology authorize the target; the executor resolves the current path.
    if selected_path != selected.identity.path:
        state["disk"] = selected.identity.path
    if platform is None:
        platform = probe_platform()
    plan = build_plan(
        state,
        selected.identity,
        platform,
        password_hash,
        disk_binding=bind_disk_topology(inventory, selected_id),
        inventory_digest=inventory.digest,
    )
    if storage_mode is InstallMode.ERASE_DISK:
        return plan

    previous_preview = state.get("guided_storage_preview_model")
    if not isinstance(previous_preview, GuidedStoragePreview):
        raise FrontendPlanError(
            "Guided storage selection is missing; rescan and select it again"
        )
    try:
        filesystem = Filesystem(str(state.get("filesystem") or "btrfs"))
        selection = replace(
            previous_preview.selection,
            filesystem=filesystem,
        )
        current_preview = build_guided_storage_preview(
            build_storage_workflow(inventory, platform),
            selection,
            swap_size_mib=(
                state.get("swap_size_mib")
                if isinstance(state.get("swap_size_mib"), int)
                else None
            ),
        )
    except (KeyError, ValueError) as error:
        raise FrontendPlanError(
            "The selected unallocated space or EFI partition changed; "
            "rescan and select it again"
        ) from error

    plan = replace(
        plan,
        storage=replace(
            plan.storage,
            mode=InstallMode.GUIDED_COEXISTENCE,
            filesystem=filesystem,
            swap_size_mib=current_preview.swap_size_mib,
            graph=current_preview.graph,
        ),
        boot=replace(plan.boot, install_fallback_path=False),
    )
    validate_plan(plan)
    state["guided_storage_preview_model"] = current_preview
    return plan


class ExecutorClient:
    """Stream one immutable plan to the privileged helper as JSON events."""

    def __init__(self, helper: str = "/usr/bin/anduinos-installer-executor"):
        self.helper = helper

    def run(
        self,
        plan: InstallPlan,
        log: Callable[[str], None],
        progress: Callable[[str, int, int], None],
        step_status: Callable[[str, str, str], None] | None = None,
    ) -> tuple[bool, str]:
        step_status = step_status or (
            lambda _step, _status, _message: None
        )
        try:
            validate_plan(plan)
        except Exception as error:
            return False, str(error)
        inhibited_helper = [
            "/usr/bin/systemd-inhibit",
            "--what=shutdown:sleep:idle",
            "--mode=block",
            "--why=Installing AnduinOS",
            self.helper,
        ]
        # The inhibitor protects the privileged executor's destructive
        # lifetime, so acquire it inside the same privilege boundary. Asking
        # the unprivileged GTK process for a block inhibitor is unreliable when
        # its helper was launched outside the graphical session scope and can
        # fail with a non-interactive polkit challenge before sudo is reached.
        command = inhibited_helper
        if os.geteuid() != 0:
            command = ["/usr/bin/sudo", "--non-interactive", *inhibited_helper]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(json.dumps(plan.to_dict()) + "\n")
            process.stdin.close()

            final_error = ""
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log(f"Malformed executor event: {line.rstrip()}")
                    continue
                kind = event.get("event")
                if kind == "log":
                    log(str(event.get("message", "")))
                elif kind == "progress":
                    progress(
                        str(event.get("step", "")),
                        int(event.get("done", 0)),
                        int(event.get("total", 1)),
                    )
                elif kind == "step-status":
                    step_status(
                        str(event.get("step", "")),
                        str(event.get("status", "")),
                        str(event.get("message", "")),
                    )
                elif kind == "complete":
                    final_error = str(event.get("error", ""))
            stderr = process.stderr.read() if process.stderr else ""
            returncode = process.wait()
            if returncode != 0:
                return False, final_error or stderr.strip() or "Executor failed"
            return True, ""
        except OSError as error:
            return False, f"Could not start privileged executor: {error}"


class DevelopmentExecutorClient:
    """Exercise the frontend contract without starting privileged code."""

    def run(
        self,
        plan: InstallPlan,
        log: Callable[[str], None],
        progress: Callable[[str, int, int], None],
        step_status: Callable[[str, str, str], None] | None = None,
    ) -> tuple[bool, str]:
        step_status = step_status or (
            lambda _step, _status, _message: None
        )
        try:
            validate_plan(plan)
        except Exception as error:
            return False, str(error)

        pipeline = describe_installation_pipeline(plan)
        total = sum(weight for _step, weight in pipeline)
        completed = 0
        log("DEVELOPMENT MODE: the privileged executor is disabled.")
        log("The immutable installation plan passed schema validation.")
        log(f"Architecture: {plan.platform.architecture.value}")
        log(
            "Firmware mode: "
            + (
                "UEFI"
                if plan.platform.firmware.value == "uefi"
                else "Legacy BIOS"
            )
        )
        log(
            "Secure Boot: "
            + plan.platform.secure_boot.value.replace("-", " ")
        )
        log(f"Selected target disk: {plan.storage.disk.path}")
        log("Other disks and EFI System Partitions are excluded")
        log(f"Target filesystem: {plan.storage.filesystem.value}")
        if plan.storage.filesystem is Filesystem.BTRFS:
            log(
                "Disk Snapshots Manager policy: retain the package copied from the Live "
                "system, verify it, and use the repository only as a legacy "
                "fallback"
            )
        else:
            log(
                "Disk Snapshots Manager policy: remove the live payload from the "
                "ext4 target"
            )
        for step, weight in pipeline:
            progress(step, completed, total)
            step_status(step, "running", "")
            log(f"[{step}] simulated; no command was executed")
            completed += weight
            time.sleep(0.03)
            step_status(step, "succeeded", "")
        progress("complete", total, total)
        log("Simulation complete. No disk, mount, firmware, or target changed.")
        return True, ""
