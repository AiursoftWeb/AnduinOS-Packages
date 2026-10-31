"""Unprivileged client for the fixed Rescue Center helper."""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any

from .i18n import _ as tr
from .live import is_live_environment, is_trusted_live_environment


HELPER = "/usr/libexec/anduinos-rescue-center-helper"
LIVE_HELPER = "/usr/libexec/anduinos-rescue-center-live-helper"


def _helper_command() -> list[str]:
    helper = LIVE_HELPER if is_live_environment() else HELPER
    return ["pkexec", helper]


def _localize_progress(message: str) -> str:
    """Translate helper-owned stages in the desktop locale, not pkexec's locale."""
    if message.startswith(("$ ", "  ")):
        return message
    if message.startswith("Completed: "):
        return tr("Completed: {command}").format(command=message.removeprefix("Completed: "))
    opening = re.fullmatch(r"Opening (\S+) and its EFI partition (\S+)", message)
    if opening:
        return tr("Opening {system} and its EFI partition {esp}").format(
            system=opening.group(1), esp=opening.group(2))
    if message.startswith("Checking installed initrd for kernel "):
        return tr("Checking installed initrd for kernel {kernel}").format(
            kernel=message.removeprefix("Checking installed initrd for kernel "))
    return tr(message)


def _localize_helper_error(message: str) -> str:
    """Translate helper-owned diagnostics after pkexec strips the desktop locale."""
    if not message:
        return ""
    dynamic = (
        (r"Boot repair command failed: (.+)", "Boot repair command failed: {command}", "command"),
        (r"Boot repair output is too large: (.+)", "Boot repair output is too large: {command}", "command"),
        (r"Installed boot repair tool is missing: /(.+)", "Installed boot repair tool is missing: /{tool}", "tool"),
        (r"Unsafe target mount directory: /(.+)", "Unsafe target mount directory: /{path}", "path"),
        (r"Mount failed: (.+)", "Mount failed: {command}", "command"),
        (r"Could not unmount recovery filesystems: (.+)", "Could not unmount recovery filesystems: {details}", "details"),
        (r"Could not unmount (.+)", "Could not unmount {mountpoint}", "mountpoint"),
        (r"Cannot export special file: (.+)", "Cannot export special file: {name}", "name"),
    )
    for pattern, source, field in dynamic:
        match = re.fullmatch(pattern, message)
        if match:
            return tr(source).format(**{field: match.group(1)})
    return tr(message)


def probe(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    result = run(
        [*_helper_command(), "probe"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(_localize_helper_error((result.stderr or result.stdout).strip()) or tr("Storage scan failed"))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(tr("The rescue helper returned invalid data")) from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError(tr("The rescue helper protocol is incompatible"))
    return payload


def inspect_target(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not path.startswith("/dev/") or len(identity) != 64:
        raise ValueError(tr("Invalid rescue target identity"))
    result = run(
        [*_helper_command(), "inspect", path, identity],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(_localize_helper_error((result.stderr or result.stdout).strip()) or tr("Target inspection failed"))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(tr("The rescue helper returned invalid data")) from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError(tr("The rescue helper protocol is incompatible"))
    return payload


def reset_password(
    path: str,
    identity: str,
    username: str,
    password: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    if not path.startswith("/dev/") or len(identity) != 64 or not username:
        raise ValueError(tr("Invalid rescue target identity"))
    if not password or len(password) > 4096 or "\n" in password or "\0" in password:
        raise ValueError(tr("The new password is empty or unsupported"))
    result = run(
        [*_helper_command(), "reset-password", path, identity, username],
        input=password,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(_localize_helper_error((result.stderr or result.stdout).strip()) or tr("Password reset failed"))


def list_files(
    path: str,
    identity: str,
    relative: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(
        ["list-files", path, identity, relative], tr("File listing failed"), run=run
    )


def export_file(
    path: str,
    identity: str,
    relative: str,
    destination: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    payload = _json_action(
        ["export", path, identity, relative, destination], tr("Export failed"), run=run,
        timeout=3600,
    )
    exported = payload.get("exported")
    if not isinstance(exported, str) or not exported:
        raise RuntimeError(tr("The rescue helper did not report the exported file"))
    return exported


def list_snapshots(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(["list-snapshots", path, identity], tr("Snapshot listing failed"), run=run)


def create_snapshot(
    path: str,
    identity: str,
    title: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not title.strip() or len(title) > 120:
        raise ValueError(tr("Invalid snapshot name"))
    return _json_action(
        ["create-snapshot", path, identity, title], tr("Snapshot creation failed"), run=run,
        timeout=3600,
    )


def restore_snapshot(
    path: str,
    identity: str,
    deployment_id: str,
    protect_current: bool,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if len(deployment_id) != 36:
        raise ValueError(tr("Invalid recovery point ID"))
    return _json_action(
        ["restore-snapshot", path, identity, deployment_id, str(protect_current).lower()],
        tr("System restore failed"),
        run=run,
        timeout=3600,
    )


def diagnose_boot(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(["diagnose-boot", path, identity], tr("Boot diagnosis failed"), run=run)


def repair_boot(
    path: str,
    identity: str,
    esp_identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if len(esp_identity) != 64:
        raise ValueError(tr("Invalid EFI partition identity"))
    if on_progress is not None:
        return _stream_json_action(
            ["repair-boot-stream", path, identity, esp_identity],
            tr("Boot repair failed"), on_progress, timeout=1800,
        )
    return _json_action(
        ["repair-boot", path, identity, esp_identity], tr("Boot repair failed"), run=run,
        timeout=1800,
    )


def _stream_json_action(
    arguments: list[str], failure: str, on_progress: Callable[[str], None],
    *, timeout: int,
) -> dict[str, Any]:
    if len(arguments) < 3 or not arguments[1].startswith("/dev/") or len(arguments[2]) != 64:
        raise ValueError(tr("Invalid rescue target identity"))
    return _read_streamed_json([*_helper_command(), *arguments], failure, on_progress, timeout)


def _read_streamed_json(
    command: list[str], failure: str, on_progress: Callable[[str], None], timeout: int,
) -> dict[str, Any]:
    """Read the helper's progress lines without blocking its final JSON pipe."""
    deadline = time.monotonic() + timeout
    output = bytearray()
    pending = bytearray()
    errors: list[str] = []

    def stderr_line(raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").strip()
        if line.startswith("RESCUE_PROGRESS\t"):
            try:
                event = json.loads(line.split("\t", 1)[1])
            except json.JSONDecodeError:
                return
            if isinstance(event, dict) and isinstance(event.get("message"), str):
                on_progress(_localize_progress(event["message"][:8192]))
        elif line:
            errors.append(line)
            if len(errors) > 20:
                del errors[:-20]

    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _mask in selector.select(timeout=min(remaining, 0.5)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif key.data == "stdout":
                            output.extend(chunk)
                            if len(output) > 1024 * 1024:
                                raise RuntimeError(tr("The rescue helper returned too much data"))
                        else:
                            pending.extend(chunk)
                            if len(pending) > 128 * 1024:
                                raise RuntimeError(tr("The rescue helper returned an oversized progress line"))
                            while b"\n" in pending:
                                line, _, rest = pending.partition(b"\n")
                                pending = bytearray(rest)
                                stderr_line(line)
                if pending:
                    stderr_line(pending)
                code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except BaseException:
                if process.poll() is None:
                    process.kill()
                process.wait()
                raise
    if code != 0:
        raise RuntimeError(_localize_helper_error("\n".join(errors)[-4000:]) or failure)
    try:
        payload = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(tr("The rescue helper returned invalid data")) from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError(tr("The rescue helper protocol is incompatible"))
    return payload


def open_emergency_terminal(
    path: str,
    identity: str,
    *,
    launch: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> None:
    if not is_trusted_live_environment():
        raise RuntimeError(tr("The emergency terminal is available only in AnduinOS Live"))
    if not path.startswith("/dev/") or len(identity) != 64:
        raise ValueError(tr("Invalid rescue target identity"))
    terminal = shutil.which("ptyxis")
    if terminal is None:
        raise RuntimeError(tr("Ptyxis is not installed in this Live session"))
    launch(
        [terminal, "--new-window", "--title", tr("Offline AnduinOS recovery"),
         "--", "pkexec", LIVE_HELPER, "shell", path, identity],
        start_new_session=True,
    )


def open_live_terminal(
    *, launch: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> None:
    if not is_trusted_live_environment():
        raise RuntimeError(tr("A verified AnduinOS Live session is required"))
    terminal = shutil.which("ptyxis")
    if terminal is None:
        raise RuntimeError(tr("Ptyxis is not installed in this Live session"))
    launch([terminal, "--new-window", "--title", tr("AnduinOS Live terminal")],
           start_new_session=True)


def _json_action(
    arguments: list[str],
    failure: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    timeout: int = 180,
) -> dict[str, Any]:
    if len(arguments) < 3 or not arguments[1].startswith("/dev/") or len(arguments[2]) != 64:
        raise ValueError(tr("Invalid rescue target identity"))
    result = run(
        [*_helper_command(), *arguments], capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(_localize_helper_error((result.stderr or result.stdout).strip()) or failure)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(tr("The rescue helper returned invalid data")) from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError(tr("The rescue helper protocol is incompatible"))
    return payload
