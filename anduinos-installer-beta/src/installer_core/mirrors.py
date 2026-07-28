"""Select and persist a fast Ubuntu archive mirror."""

from __future__ import annotations

import os
import re
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import Architecture
from .steps import FailurePolicy, InstallContext
from .validation import validate_plan


MIRRORS = (
    "https://archive.ubuntu.com/ubuntu/",
    "http://us.archive.ubuntu.com/ubuntu/",
    "http://azure.archive.ubuntu.com/ubuntu/",
    "https://mirror.aarnet.edu.au/pub/ubuntu/archive/",
    "https://mirror.fsmg.org.nz/ubuntu/",
    "https://mirror.2degrees.nz/ubuntu/",
    "https://ubuntu.lagoon.nc/ubuntu/",
    "https://mirror.xtom.com.hk/ubuntu/",
    "https://mirror.01link.hk/ubuntu/",
    "https://ftp.udx.icscoe.jp/Linux/ubuntu/",
    "https://ftp.kaist.ac.kr/ubuntu/",
    "http://jp.archive.ubuntu.com/ubuntu/",
    "http://kr.archive.ubuntu.com/ubuntu/",
    "http://tw.archive.ubuntu.com/ubuntu/",
    "https://mirror.twds.com.tw/ubuntu/",
    "http://mirrors.ustc.edu.cn/ubuntu/",
    "http://ftp.sjtu.edu.cn/ubuntu/",
    "http://mirrors.tuna.tsinghua.edu.cn/ubuntu/",
    "http://mirrors.aliyun.com/ubuntu/",
    "http://mirrors.cloud.tencent.com/ubuntu/",
    "http://mirrors.huaweicloud.com/ubuntu/",
    "http://mirrors.zju.edu.cn/ubuntu/",
    "https://mirror.nju.edu.cn/ubuntu/",
    "https://mirrors.bfsu.edu.cn/ubuntu/",
    "http://sg.archive.ubuntu.com/ubuntu/",
    "https://mirror.sg.gs/ubuntu/",
    "https://mirror.kku.ac.th/ubuntu/",
    "https://mirror.bizflycloud.vn/ubuntu/",
    "https://mirrors.nxtgen.com/ubuntu-mirror/ubuntu/",
    "https://ubuntu.mobinhost.com/ubuntu/",
    "https://mirror.iranserver.com/ubuntu/",
    "https://mirror.maeen.sa/apt-mirror/",
    "https://mirrors.dotsrc.org/ubuntu/",
    "https://mirrors.nic.funet.fi/ubuntu/",
    "https://ftp.acc.umu.se/ubuntu/",
    "https://mirrors.xtom.ee/ubuntu/",
    "https://mirror.ubuntu.ikoula.com/",
    "https://ftp.uni-stuttgart.de/ubuntu/",
    "https://mirror.i3d.net/pub/ubuntu/",
    "https://mirrors.xtom.nl/ubuntu/",
    "https://mirror.init7.net/ubuntu/",
    "https://mirror.cov.ukservers.com/ubuntu/",
    "https://mirrors.ukfast.co.uk/sites/archive.ubuntu.com/",
    "https://ubuntu.mirror.garr.it/ubuntu/",
    "https://mirror.raiolanetworks.com/ubuntu/",
    "https://mirrors.up.pt/ubuntu/",
    "https://mirror.alastyr.com/ubuntu/ubuntu-archive/",
    "https://mirrors.neterra.net/ubuntu/archive/",
    "https://ftp.icm.edu.pl/pub/Linux/ubuntu/",
    "https://ftp.psnc.pl/linux/ubuntu/",
    "https://ubuntu.anexia.at/ubuntu/",
    "https://mirror.team-host.ru/ubuntu/",
    "https://mirror.csclub.uwaterloo.ca/ubuntu/",
    "https://mirrors.iu13.net/ubuntu/",
    "https://mirror.tzulo.com/ubuntu/",
    "https://mirror.pilotfiber.com/ubuntu/",
    "https://mirror.us.mirhosting.net/ubuntu/",
    "http://mirror.math.princeton.edu/pub/ubuntu/",
    "http://mirror.pit.teraswitch.com/ubuntu/",
    "https://mirror.fcix.net/ubuntu/",
    "https://mirror.its.umich.edu/ubuntu/",
    "http://mirrors.mit.edu/ubuntu/",
    "http://www.gtlib.gatech.edu/pub/ubuntu/",
    "http://ubuntu.osuosl.org/ubuntu/",
    "https://mirror.uepg.br/ubuntu/",
)


@dataclass(frozen=True)
class MirrorMeasurement:
    uri: str
    latency_ms: float
    bandwidth_mbps: float


def select_fastest_mirror(
    codename: str,
    architecture: Architecture,
    *,
    candidates: tuple[str, ...] = MIRRORS,
    opener: Callable[..., object] = urllib.request.urlopen,
    clock: Callable[[], float] = time.monotonic,
) -> MirrorMeasurement:
    """Probe latency concurrently, then bandwidth-test the best five."""

    def latency(uri: str) -> tuple[str, float | None]:
        request = urllib.request.Request(
            f"{uri}dists/{codename}/Release", method="HEAD"
        )
        started = clock()
        try:
            response = opener(request, timeout=3)
            status = getattr(response, "status", 200)
            response.close()
            if status == 200:
                return uri, (clock() - started) * 1000
        except Exception:
            pass
        return uri, None

    reachable: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(latency, uri) for uri in candidates]
        for future in as_completed(futures):
            uri, elapsed = future.result()
            if elapsed is not None:
                reachable.append((uri, elapsed))
    if not reachable:
        raise RuntimeError("No Ubuntu archive mirror is reachable")
    reachable.sort(key=lambda item: (item[1], not item[0].startswith("https://")))
    finalists = reachable[:5]

    arch = architecture.value

    def bandwidth(uri: str) -> tuple[str, float]:
        urls = (
            f"{uri}dists/{codename}/main/binary-{arch}/Packages.gz",
            f"{uri}dists/{codename}/Contents-amd64.gz",
        )
        for url in urls:
            try:
                started = clock()
                response = opener(urllib.request.Request(url), timeout=5)
                if getattr(response, "status", 200) != 200:
                    response.close()
                    continue
                size = 0
                while clock() - started < 3.0:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                response.close()
                elapsed = clock() - started
                if size and elapsed > 0:
                    return uri, size * 8 / elapsed / 1024 / 1024
            except Exception:
                continue
        return uri, 0.0

    speeds: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(bandwidth, uri) for uri, _ in finalists]
        for future in as_completed(futures):
            uri, speed = future.result()
            speeds[uri] = speed

    latency_by_uri = dict(finalists)
    best = min(
        (uri for uri, _latency in finalists),
        key=lambda uri: (
            -speeds.get(uri, 0.0),
            latency_by_uri[uri],
            not uri.startswith("https://"),
        ),
    )
    return MirrorMeasurement(best, latency_by_uri[best], speeds.get(best, 0.0))


@dataclass
class SelectFastestAptMirrorStep:
    id: str = "select-fastest-apt-mirror"
    title: str = "Select the fastest package mirror"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 3
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        validate_plan(context.plan)

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        source = target / "etc/apt/sources.list.d/ubuntu.sources"
        if not source.is_file():
            raise RuntimeError("Target Ubuntu Deb822 source file is missing")
        codename = _target_codename(target)
        measurement = select_fastest_mirror(
            codename, context.plan.platform.architecture
        )
        original = source.read_bytes()
        original_mode = source.stat().st_mode & 0o777
        updated = _replace_uris(original.decode("utf-8"), measurement.uri)
        context.values["apt_mirror_source"] = source
        context.values["apt_mirror_original"] = original
        context.values["apt_mirror_original_mode"] = original_mode
        context.values["apt_mirror_selected"] = measurement.uri
        _atomic_write(source, updated.encode("utf-8"), original_mode)
        context.log(
            "Selected Ubuntu mirror "
            f"{measurement.uri} ({measurement.latency_ms:.0f} ms, "
            f"{measurement.bandwidth_mbps:.1f} Mbps)"
        )

    def verify(self, context: InstallContext) -> None:
        source = context.values.get("apt_mirror_source")
        selected = context.values.get("apt_mirror_selected")
        if not isinstance(source, Path) or not isinstance(selected, str):
            raise RuntimeError("Mirror selection was not persisted")
        if selected not in source.read_text(encoding="utf-8"):
            raise RuntimeError("Selected mirror is absent from Ubuntu sources")

    def cleanup(self, context: InstallContext) -> None:
        return None


def restore_original_mirror(context: InstallContext) -> bool:
    source = context.values.get("apt_mirror_source")
    original = context.values.get("apt_mirror_original")
    mode = context.values.get("apt_mirror_original_mode")
    if not isinstance(source, Path) or not isinstance(original, bytes):
        return False
    _atomic_write(source, original, mode if isinstance(mode, int) else 0o644)
    context.values["apt_mirror_rolled_back"] = True
    return True


def _replace_uris(content: str, mirror: str) -> str:
    updated, count = re.subn(
        r"(?m)^(\s*URIs:\s*).+$",
        lambda match: match.group(1) + mirror,
        content,
    )
    if count == 0:
        raise RuntimeError("Ubuntu Deb822 source contains no URIs field")
    return updated


def _target_codename(target: Path) -> str:
    os_release = (target / "etc/os-release").read_text(encoding="utf-8")
    match = re.search(
        r'(?m)^VERSION_CODENAME=["\']?([a-z0-9][a-z0-9-]*)["\']?$',
        os_release,
    )
    if not match:
        raise RuntimeError("Target VERSION_CODENAME is missing or invalid")
    return match.group(1)


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
