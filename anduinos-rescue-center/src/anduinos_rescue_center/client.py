"""Unprivileged client for the fixed Rescue Center helper."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any

from .live import is_live_environment, is_trusted_live_environment


HELPER = "/usr/libexec/anduinos-rescue-center-helper"
LIVE_HELPER = "/usr/libexec/anduinos-rescue-center-live-helper"


def _helper_command() -> list[str]:
    helper = LIVE_HELPER if is_live_environment() else HELPER
    return ["pkexec", helper]


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
        raise RuntimeError((result.stderr or result.stdout).strip() or "Storage scan failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
    return payload


def inspect_target(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not path.startswith("/dev/") or len(identity) != 64:
        raise ValueError("Invalid rescue target identity")
    result = run(
        [*_helper_command(), "inspect", path, identity],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Target inspection failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
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
        raise ValueError("Invalid rescue target identity")
    if not password or len(password) > 4096 or "\n" in password or "\0" in password:
        raise ValueError("The new password is empty or unsupported")
    result = run(
        [*_helper_command(), "reset-password", path, identity, username],
        input=password,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Password reset failed")


def list_files(
    path: str,
    identity: str,
    relative: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(
        ["list-files", path, identity, relative], "File listing failed", run=run
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
        ["export", path, identity, relative, destination], "Export failed", run=run,
        timeout=3600,
    )
    exported = payload.get("exported")
    if not isinstance(exported, str) or not exported:
        raise RuntimeError("The rescue helper did not report the exported file")
    return exported


def list_snapshots(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(["list-snapshots", path, identity], "Snapshot listing failed", run=run)


def create_snapshot(
    path: str,
    identity: str,
    title: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not title.strip() or len(title) > 120:
        raise ValueError("Invalid snapshot name")
    return _json_action(
        ["create-snapshot", path, identity, title], "Snapshot creation failed", run=run,
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
        raise ValueError("Invalid recovery point ID")
    return _json_action(
        ["restore-snapshot", path, identity, deployment_id, str(protect_current).lower()],
        "System restore failed",
        run=run,
        timeout=3600,
    )


def diagnose_boot(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(["diagnose-boot", path, identity], "Boot diagnosis failed", run=run)


def repair_boot(
    path: str,
    identity: str,
    esp_identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if len(esp_identity) != 64:
        raise ValueError("Invalid EFI partition identity")
    if on_progress is not None:
        return _stream_json_action(
            ["repair-boot-stream", path, identity, esp_identity],
            "Boot repair failed", on_progress, timeout=1800,
        )
    return _json_action(
        ["repair-boot", path, identity, esp_identity], "Boot repair failed", run=run,
        timeout=1800,
    )


def _stream_json_action(
    arguments: list[str], failure: str, on_progress: Callable[[str], None],
    *, timeout: int,
) -> dict[str, Any]:
    if len(arguments) < 3 or not arguments[1].startswith("/dev/") or len(arguments[2]) != 64:
        raise ValueError("Invalid rescue target identity")
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
                on_progress(event["message"][:8192])
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
                                raise RuntimeError("The rescue helper returned too much data")
                        else:
                            pending.extend(chunk)
                            if len(pending) > 128 * 1024:
                                raise RuntimeError("The rescue helper returned an oversized progress line")
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
        raise RuntimeError("\n".join(errors)[-4000:] or failure)
    try:
        payload = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
    return payload


def open_emergency_terminal(
    path: str,
    identity: str,
    *,
    launch: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> None:
    if not is_trusted_live_environment():
        raise RuntimeError("The emergency terminal is available only in AnduinOS Live")
    if not path.startswith("/dev/") or len(identity) != 64:
        raise ValueError("Invalid rescue target identity")
    terminal = shutil.which("ptyxis")
    if terminal is None:
        raise RuntimeError("Ptyxis is not installed in this Live session")
    launch(
        [terminal, "--new-window", "--title", "Offline AnduinOS recovery",
         "--", "pkexec", LIVE_HELPER, "shell", path, identity],
        start_new_session=True,
    )


def open_live_terminal(
    *, launch: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> None:
    if not is_trusted_live_environment():
        raise RuntimeError("A verified AnduinOS Live session is required")
    terminal = shutil.which("ptyxis")
    if terminal is None:
        raise RuntimeError("Ptyxis is not installed in this Live session")
    launch([terminal, "--new-window", "--title", "AnduinOS Live terminal"],
           start_new_session=True)


def _json_action(
    arguments: list[str],
    failure: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    timeout: int = 180,
) -> dict[str, Any]:
    if len(arguments) < 3 or not arguments[1].startswith("/dev/") or len(arguments[2]) != 64:
        raise ValueError("Invalid rescue target identity")
    result = run(
        [*_helper_command(), *arguments], capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or failure)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
    return payload
