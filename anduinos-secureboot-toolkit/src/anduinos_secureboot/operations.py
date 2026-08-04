"""Fixed privileged operations exposed by the Secure Boot helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Callable, Sequence

from .inspect import Runner, certificate_enrolled, certificate_pending


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


def prepare(
    run: Run = run_command,
    private_key: Path = MOK_PRIVATE_KEY,
    certificate: Path = MOK_CERTIFICATE,
    config: Path = DKMS_CONFIG,
) -> OperationResult:
    result = OperationResult("prepare")

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

    rebuilt = run(["dkms", "autoinstall"])
    result.steps["modules_rebuilt"] = StepResult(
        "success" if rebuilt.returncode == 0 else "failed", _detail(rebuilt)
    )
    return result


def repair_dkms(
    run: Run = run_command,
    private_key: Path = MOK_PRIVATE_KEY,
    certificate: Path = MOK_CERTIFICATE,
    config: Path = DKMS_CONFIG,
) -> OperationResult:
    result = OperationResult("repair-dkms")
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
    rebuilt = run(["dkms", "autoinstall", "--force"])
    result.steps["modules_rebuilt"] = StepResult(
        "success" if rebuilt.returncode == 0 else "failed", _detail(rebuilt)
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
