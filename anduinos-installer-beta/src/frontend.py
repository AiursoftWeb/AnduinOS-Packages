"""Unprivileged GTK-to-executor boundary."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable

from installer_core.model import InstallPlan
from installer_core.passwords import hash_password
from installer_core.planning import build_plan
from installer_core.probe import probe_disks, probe_platform
from installer_core.validation import validate_plan


class FrontendPlanError(RuntimeError):
    pass


def create_install_plan(state: dict[str, object]) -> InstallPlan:
    password = str(state.get("password") or "")
    confirmation = str(state.get("password_confirmation") or "")
    passwordless = not password and not confirmation
    try:
        if passwordless:
            if not bool(state.get("sudo_without_password")):
                raise FrontendPlanError(
                    "An account without a password requires passwordless sudo"
                )
            password_hash = ""
        else:
            if password != confirmation:
                raise FrontendPlanError("The two passwords do not match")
            password_hash = hash_password(password)
        state["passwordless_shared"] = passwordless
    finally:
        # Plaintext exists only while the account page and this call need it.
        state["password"] = ""
        state["password_confirmation"] = ""
        clear_ui = state.pop("_clear_password_ui", None)
        if callable(clear_ui):
            clear_ui()

    selected_path = str(state.get("disk") or "")
    selected_id = str(state.get("disk_stable_id") or "")
    selected_size = int(state.get("disk_size_bytes") or 0)
    disk = next(
        (
            item
            for item in probe_disks()
            if item.path == selected_path
            and item.stable_id == selected_id
            and item.expected_size_bytes == selected_size
        ),
        None,
    )
    if disk is None:
        raise FrontendPlanError(
            "The selected disk changed or disappeared; select it again"
        )
    return build_plan(state, disk, probe_platform(), password_hash)


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
        ("verify-environment", 1),
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
        if plan.software.install_updates:
            pipeline.extend(
                (("refresh-package-indexes", 2), ("upgrade-system", 8))
            )
        pipeline.append(("prepare-secure-boot", 4))
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
