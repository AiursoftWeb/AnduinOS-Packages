"""Fixed privileged operations exposed by the Secure Boot helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Sequence

from .inspect import (
    Runner,
    certificate_enrolled,
    certificate_pending,
    parse_secure_boot_status,
)
from .model import SecureBootStatus


ENROLLMENT_PASSWORD = "123456"
MOK_DIRECTORY = Path("/var/lib/shim-signed/mok")
MOK_PRIVATE_KEY = MOK_DIRECTORY / "MOK.priv"
MOK_CERTIFICATE = MOK_DIRECTORY / "MOK.der"
DKMS_CONFIG = Path("/etc/dkms/framework.conf.d/anduinos-sb-sign.conf")
LOCK_FILE = Path("/run/lock/anduinos-secureboot-toolkit.lock")
CONFIG_CONTENT = (
    'mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"\n'
    'mok_certificate="/var/lib/shim-signed/mok/MOK.der"\n'
)


@dataclass(frozen=True)
class StepResult:
    status: str
    detail: str = ""


@dataclass
class OperationResult:
    operation: str
    steps: dict[str, StepResult] = field(default_factory=dict)
    reboot_required: bool = False
    schema: int = 1

    @property
    def ok(self) -> bool:
        return all(step.status in {"success", "skipped"} for step in self.steps.values())

    def to_json(self) -> str:
        payload = asdict(self)
        payload["ok"] = self.ok
        return json.dumps(payload, sort_keys=True)


Run = Callable[..., subprocess.CompletedProcess[str]]
CommandAvailable = Callable[[str], bool]


@dataclass(frozen=True, order=True)
class DkmsTarget:
    module: str
    version: str
    kernel: str
    architecture: str


_DKMS_STATUS = re.compile(
    r"^(?P<module>[^/,\s]+)/(?P<version>[^,\s]+),\s*"
    r"(?P<kernel>[^,\s]+),\s*(?P<architecture>[^:\s]+):\s*"
    r"(?P<state>[a-z-]+)(?:\s.*)?$"
)


class CallableRunner(Runner):
    """Adapt the injectable privileged command callback to the inspector API."""

    def __init__(self, callback: Run):
        self.callback = callback

    def run(
        self, command: Sequence[str], timeout: int = 10
    ) -> subprocess.CompletedProcess[str]:
        return self.callback(command, timeout=timeout)


def run_command(
    command: Sequence[str], *, stdin: str | None = None, timeout: int = 1800
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout).strip()


def command_available(command: str) -> bool:
    return shutil.which(command) is not None


def parse_installed_dkms_targets(
    output: str, kernel_release: str
) -> tuple[DkmsTarget, ...]:
    """Parse installed modules for one kernel from C-locale `dkms status`."""

    targets: set[DkmsTarget] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _DKMS_STATUS.fullmatch(line)
        if match is None:
            if re.search(r":\s*installed(?:\s|$)", line):
                raise ValueError(f"unrecognized installed DKMS status: {line}")
            continue
        if match.group("state") != "installed":
            continue
        target = DkmsTarget(
            match.group("module"),
            match.group("version"),
            match.group("kernel"),
            match.group("architecture"),
        )
        if target.kernel == kernel_release:
            targets.add(target)
    return tuple(sorted(targets))


def _target_label(target: DkmsTarget) -> str:
    return (
        f"{target.module}/{target.version} for "
        f"{target.kernel}/{target.architecture}"
    )


def _rebuild_installed_dkms_modules(
    result: OperationResult,
    run: Run,
    *,
    kernel_release: str,
    available: CommandAvailable,
) -> None:
    if not available("dkms"):
        result.steps["modules_rebuilt"] = StepResult(
            "skipped", "DKMS is not installed; no registered modules were rebuilt"
        )
        return

    status = run(["dkms", "status", "-k", kernel_release], timeout=120)
    if status.returncode:
        result.steps["modules_rebuilt"] = StepResult("failed", _detail(status))
        return
    try:
        targets = parse_installed_dkms_targets(status.stdout, kernel_release)
    except ValueError as error:
        result.steps["modules_rebuilt"] = StepResult("failed", str(error))
        return
    if not targets:
        result.steps["modules_rebuilt"] = StepResult(
            "skipped", f"No installed DKMS modules target kernel {kernel_release}"
        )
        return

    rebuilt: list[str] = []
    failures: list[str] = []
    for target in targets:
        common = [
            "-m",
            target.module,
            "-v",
            target.version,
            "-k",
            target.kernel,
            "-a",
            target.architecture,
        ]
        built = run(["dkms", "build", "--force", *common], timeout=1800)
        if built.returncode:
            failures.append(
                f"build {_target_label(target)}: "
                f"{_detail(built) or f'exit status {built.returncode}'}"
            )
            continue
        installed = run(["dkms", "install", "--force", *common], timeout=1800)
        if installed.returncode:
            failures.append(
                f"install {_target_label(target)}: "
                f"{_detail(installed) or f'exit status {installed.returncode}'}"
            )
            continue
        rebuilt.append(_target_label(target))

    if failures:
        detail = "; ".join(failures)
        if rebuilt:
            detail = f"rebuilt {', '.join(rebuilt)}; failed: {detail}"
        result.steps["modules_rebuilt"] = StepResult("failed", detail)
        return
    result.steps["modules_rebuilt"] = StepResult(
        "success", f"Rebuilt and reinstalled {', '.join(rebuilt)}"
    )


def _write_signing_config(path: Path = DKMS_CONFIG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(CONFIG_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _firmware_enforces_secure_boot(result: OperationResult, run: Run) -> bool:
    probe = run(["mokutil", "--sb-state"], timeout=10)
    status = parse_secure_boot_status(probe)
    if status is SecureBootStatus.ENABLED:
        result.steps["firmware_state"] = StepResult("success", status.value)
        return True
    if status in {SecureBootStatus.DISABLED, SecureBootStatus.UNSUPPORTED}:
        result.steps["firmware_state"] = StepResult("skipped", status.value)
        return False
    result.steps["firmware_state"] = StepResult(
        "failed", "Secure Boot state could not be determined"
    )
    return False


def prepare(
    run: Run = run_command,
    private_key: Path = MOK_PRIVATE_KEY,
    certificate: Path = MOK_CERTIFICATE,
    config: Path = DKMS_CONFIG,
    *,
    kernel_release: str | None = None,
    available: CommandAvailable = command_available,
) -> OperationResult:
    result = OperationResult("prepare")
    if not _firmware_enforces_secure_boot(result, run):
        return result

    if private_key.is_file() and certificate.is_file():
        result.steps["key_created"] = StepResult("skipped", "key pair already exists")
    else:
        generated = run(["update-secureboot-policy", "--new-key"])
        if generated.returncode or not (private_key.is_file() and certificate.is_file()):
            result.steps["key_created"] = StepResult("failed", _detail(generated))
            return result
        result.steps["key_created"] = StepResult("success")

    try:
        _write_signing_config(config)
        result.steps["configuration_written"] = StepResult("success")
    except OSError as error:
        result.steps["configuration_written"] = StepResult("failed", str(error))
        return result

    inspector = CallableRunner(run)
    if certificate_enrolled(certificate, inspector):
        result.steps["enrollment_queued"] = StepResult(
            "skipped", "certificate is already enrolled"
        )
    else:
        if certificate_pending(certificate, inspector):
            result.steps["enrollment_queued"] = StepResult(
                "skipped", "an enrollment request is already pending"
            )
        else:
            queued = run(
                ["mokutil", "--import", str(certificate)],
                stdin=f"{ENROLLMENT_PASSWORD}\n{ENROLLMENT_PASSWORD}\n",
                timeout=120,
            )
            if queued.returncode:
                result.steps["enrollment_queued"] = StepResult("failed", _detail(queued))
                return result
            result.steps["enrollment_queued"] = StepResult("success")
        result.reboot_required = True

    _rebuild_installed_dkms_modules(
        result,
        run,
        kernel_release=kernel_release or os.uname().release,
        available=available,
    )
    return result


def repair_dkms(
    run: Run = run_command,
    private_key: Path = MOK_PRIVATE_KEY,
    certificate: Path = MOK_CERTIFICATE,
    config: Path = DKMS_CONFIG,
    *,
    kernel_release: str | None = None,
    available: CommandAvailable = command_available,
) -> OperationResult:
    result = OperationResult("repair-dkms")
    if not _firmware_enforces_secure_boot(result, run):
        return result
    if private_key.is_file() and certificate.is_file():
        try:
            _write_signing_config(config)
            result.steps["configuration_written"] = StepResult("success")
        except OSError as error:
            result.steps["configuration_written"] = StepResult("failed", str(error))
            return result
    else:
        result.steps["configuration_written"] = StepResult(
            "failed", "MOK key pair is missing"
        )
        return result
    _rebuild_installed_dkms_modules(
        result,
        run,
        kernel_release=kernel_release or os.uname().release,
        available=available,
    )
    return result


def execute(action: str, run: Run = run_command) -> OperationResult:
    if action == "prepare":
        return prepare(run)
    if action == "repair-dkms":
        return repair_dkms(run)
    raise ValueError(f"unsupported action: {action}")


def helper_main(arguments: list[str]) -> int:
    if os.geteuid() != 0:
        print(json.dumps({"schema": 1, "error": "root-required"}))
        return 77
    if len(arguments) != 1 or arguments[0] not in {"prepare", "repair-dkms"}:
        print(json.dumps({"schema": 1, "error": "unsupported-action"}))
        return 64

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"schema": 1, "error": "operation-in-progress"}))
            return 75
        try:
            result = execute(arguments[0])
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            print(json.dumps({"schema": 1, "error": str(error)}))
            return 1
        print(result.to_json())
        return 0 if result.ok else 1
