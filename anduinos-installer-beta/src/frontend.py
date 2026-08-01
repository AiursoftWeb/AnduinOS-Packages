"""Unprivileged GTK-to-executor boundary."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import replace
from enum import Enum

from installer_core.model import Filesystem, InstallMode, InstallPlan
from installer_core.passwords import hash_password
from installer_core.planning import build_plan
from installer_core.probe import probe_platform
from installer_core.storage_inventory import (
    bind_disk_topology,
    probe_storage_inventory,
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


def _clear_plaintext_passwords(state: dict[str, object]) -> None:
    state["password"] = ""
    state["password_confirmation"] = ""
    clear_ui = state.pop("_clear_password_ui", None)
    if callable(clear_ui):
        clear_ui()


def create_install_plan(state: dict[str, object]) -> InstallPlan:
    try:
        storage_mode = InstallMode(
            str(state.get("storage_mode", InstallMode.ERASE_DISK.value))
        )
    except ValueError as error:
        _clear_plaintext_passwords(state)
        raise FrontendPlanError("Unsupported storage mode") from error
    if storage_mode not in {
        InstallMode.ERASE_DISK,
        InstallMode.GUIDED_COEXISTENCE,
    }:
        _clear_plaintext_passwords(state)
        raise FrontendPlanError("Unsupported storage mode")

    password = str(state.get("password") or "")
    confirmation = str(state.get("password_confirmation") or "")
    passwordless = not password and not confirmation
    try:
        if passwordless:
            if not bool(state.get("sudo_without_password")):
                raise FrontendPlanError(
                    "An account without a password requires passwordless sudo"
                )
            if storage_mode is InstallMode.GUIDED_COEXISTENCE:
                raise FrontendPlanError(
                    "Install alongside requires a password-protected account"
                )
            password_hash = ""
        else:
            if password != confirmation:
                raise FrontendPlanError("The two passwords do not match")
            password_hash = hash_password(password)
        state["passwordless_shared"] = passwordless
    finally:
        # Plaintext exists only while the account page and this call need it.
        _clear_plaintext_passwords(state)

    selected_path = str(state.get("disk") or "")
    selected_id = str(state.get("disk_stable_id") or "")
    selected_size = int(state.get("disk_size_bytes") or 0)
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
    # The path selected by GTK is a display hint only. Stable identity and
    # topology authorize the target; the executor resolves the current path.
    if selected_path != selected.identity.path:
        state["disk"] = selected.identity.path
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
        helper_command = [self.helper]
        if os.geteuid() != 0:
            helper_command = ["sudo", "--non-interactive", *helper_command]
        command = [
            "systemd-inhibit",
            "--what=shutdown:sleep:idle",
            "--mode=block",
            "--why=Installing AnduinOS",
            *helper_command,
        ]
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

    BASE_PIPELINE = (
        ("detect-boot-environment", 1),
        ("detect-network-connectivity", 1),
        ("verify-target-disk", 1),
        ("prepare-storage", 10),
        ("mount-target", 3),
        ("copy-system", 60),
        ("configure-storage", 3),
        ("enter-chroot", 2),
        ("cleanup-live-system", 4),
        ("configure-system", 5),
        ("select-fastest-apt-mirror", 3),
    )

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

        pipeline = list(self.BASE_PIPELINE)
        # Match InstallerExecutor exactly: Secure Boot key preparation must
        # precede upgrades and third-party drivers so any DKMS modules created
        # by those operations can be signed immediately.
        pipeline.append(("prepare-secure-boot", 4))
        if plan.software.install_updates:
            pipeline.extend(
                (("refresh-package-indexes", 2), ("upgrade-system", 8))
            )
        if plan.software.install_third_party_drivers:
            pipeline.append(("install-third-party-drivers", 8))
        pipeline.extend(
            (
                ("verify-dkms-signatures", 3),
                ("install-bootloader", 5),
                ("enroll-secure-boot", 2),
                ("leave-chroot", 1),
                ("unmount-target", 1),
            )
        )
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
