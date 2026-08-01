"""Non-serializable authorization gates for disposable guided-test VMs."""

from __future__ import annotations

import os
import subprocess

from .model import InstallPlan
from .validation import ExecutionPolicy


GUIDED_TEST_FLAG = "--guided-destructive-test"
GUIDED_TEST_ENVIRONMENT = "ANDUINOS_INSTALLER_DESTRUCTIVE_TEST"
ALLOWED_TEST_VIRTUALIZATIONS = {"kvm", "qemu"}


def execution_policy(
    arguments: list[str],
    environment: dict[str, str],
) -> ExecutionPolicy:
    """Require two executor-owned opt-ins for destructive coexistence tests."""

    unknown = [item for item in arguments if item != GUIDED_TEST_FLAG]
    if unknown:
        raise ValueError("Unknown executor argument: " + unknown[0])
    requested = GUIDED_TEST_FLAG in arguments
    authorized = environment.get(GUIDED_TEST_ENVIRONMENT) == "1"
    if requested != authorized:
        raise ValueError(
            "Guided destructive tests require both the executor flag and "
            "the disposable-VM environment marker"
        )
    return (
        ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST
        if requested
        else ExecutionPolicy.RELEASE
    )


def detect_virtualization() -> str:
    result = subprocess.run(
        ("systemd-detect-virt", "--vm"),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.stdout.strip().casefold() if result.returncode == 0 else ""


def require_disposable_guided_vm(
    plan: InstallPlan,
    *,
    environment: dict[str, str] | None = None,
    effective_uid: int | None = None,
    virtualization: str | None = None,
) -> None:
    """Fail unless evidence tooling is inside the exact disposable VM shape."""

    require_disposable_test_environment(
        plan.storage.disk.path,
        environment=environment,
        effective_uid=effective_uid,
        virtualization=virtualization,
    )


def require_disposable_test_environment(
    target_path: str,
    *,
    environment: dict[str, str] | None = None,
    effective_uid: int | None = None,
    virtualization: str | None = None,
) -> None:
    actual_environment = (
        dict(os.environ) if environment is None else environment
    )
    actual_uid = os.geteuid() if effective_uid is None else effective_uid
    actual_virtualization = (
        detect_virtualization()
        if virtualization is None
        else virtualization.casefold()
    )
    if actual_environment.get(GUIDED_TEST_ENVIRONMENT) != "1":
        raise RuntimeError("Disposable-VM environment marker is missing")
    if actual_uid != 0:
        raise RuntimeError("Guided VM evidence tooling must run as root")
    if actual_virtualization not in ALLOWED_TEST_VIRTUALIZATIONS:
        raise RuntimeError("Guided evidence tooling requires a QEMU/KVM VM")
    if target_path != "/dev/vda":
        raise RuntimeError("Guided VM evidence tooling requires /dev/vda")
