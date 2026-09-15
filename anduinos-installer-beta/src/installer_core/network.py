"""Detect whether online-only installation work is currently possible."""

from __future__ import annotations

import re
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .steps import FailurePolicy, InstallContext, StepSkipped


CONNECTIVITY_ENDPOINTS = (
    "https://archive.ubuntu.com/ubuntu/",
    "http://archive.ubuntu.com/ubuntu/",
    "http://mirror.aiursoft.com/ubuntu/",
    "http://ports.ubuntu.com/ubuntu-ports/",
)


def probe_ubuntu_archive(
    codename: str,
    *,
    candidates: tuple[str, ...] = CONNECTIVITY_ENDPOINTS,
    opener: Callable[..., object] = urllib.request.urlopen,
    log: Callable[[str], None] = lambda _message: None,
) -> str | None:
    """Return a verified Ubuntu archive endpoint, or ``None`` when offline."""

    codename_line = re.compile(
        rf"(?m)^Codename:\s*{re.escape(codename)}\s*$"
    )
    diagnostics: list[str] = []

    def probe(base_uri: str) -> str | None:
        started = time.monotonic()
        outcome = "no matching release metadata"
        request = urllib.request.Request(
            f"{base_uri}dists/{codename}/Release",
            headers={
                "Range": "bytes=0-8191",
                "User-Agent": "AnduinOS-Installer/2",
            },
        )
        try:
            response = opener(request, timeout=4)
            try:
                status = getattr(response, "status", 200)
                payload = response.read(8192)
            finally:
                response.close()
            outcome = f"HTTP {status}; codename mismatch (expected {codename})"
            if status not in (200, 206):
                outcome = f"unexpected HTTP {status}"
                return None
            release = payload.decode("utf-8", errors="replace")
            if codename_line.search(release):
                outcome = f"HTTP {status}; verified codename {codename}"
                return base_uri
        except Exception as error:
            outcome = f"{type(error).__name__}: {error}"
        finally:
            diagnostics.append(
                f"Network probe {request.full_url}: {outcome}; "
                f"elapsed={time.monotonic() - started:.3f}s"
            )
        return None

    selected = None
    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        futures = [executor.submit(probe, uri) for uri in candidates]
        for future in as_completed(futures):
            endpoint = future.result()
            if selected is None and endpoint is not None:
                selected = endpoint
    # Keep the executor's JSON-lines logger on the calling thread. Concurrent
    # print calls can interleave and corrupt the privileged helper protocol.
    for message in diagnostics:
        log(message)
    return selected


@dataclass
class DetectNetworkConnectivityStep:
    id: str = "detect-network-connectivity"
    title: str = "Detect Internet connectivity"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 1
    destructive: bool = False
    os_release: Path = Path("/etc/os-release")
    detector: Callable[[str], str | None] | None = None

    def preflight(self, context: InstallContext) -> None:
        return None

    def execute(self, context: InstallContext) -> None:
        context.values["network_online"] = False
        context.values["network_endpoint"] = None
        codename = _codename(self.os_release)
        endpoint = (
            self.detector(codename)
            if self.detector is not None
            else probe_ubuntu_archive(codename, log=context.log)
        )
        if endpoint is None:
            raise RuntimeError(
                "Offline mode detected; online-only installation steps "
                "will be skipped"
            )
        context.values["network_online"] = True
        context.values["network_endpoint"] = endpoint
        context.log(f"Internet connectivity: online via {endpoint}")

    def verify(self, context: InstallContext) -> None:
        if context.values.get("network_online") is not True:
            raise RuntimeError("Online connectivity was not persisted")

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class RecheckNetworkConnectivityStep(DetectNetworkConnectivityStep):
    """One recovery attempt at the download boundary, never per package."""

    id: str = "recheck-network-connectivity"

    def execute(self, context: InstallContext) -> None:
        if context.values.get("network_online") is not False:
            raise StepSkipped("Initial connectivity check succeeded; no retry needed")
        context.log("Rechecking connectivity once before package downloads")
        super().execute(context)


def _codename(os_release: Path) -> str:
    content = os_release.read_text(encoding="utf-8")
    match = re.search(
        r'(?m)^VERSION_CODENAME=["\']?([a-z0-9][a-z0-9-]*)["\']?$',
        content,
    )
    if not match:
        raise RuntimeError("Live VERSION_CODENAME is missing or invalid")
    return match.group(1)
