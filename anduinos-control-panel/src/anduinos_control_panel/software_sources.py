"""Software-source and system-update settings for AnduinOS Control Panel."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import gettext
import configparser
import gzip
import hashlib
import io
import os
from pathlib import Path
import platform
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import zlib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk


LOCALE_DIR = "/usr/share/locale"
TEXT_DOMAIN = "anduinos-control-panel"
HELPER = "/usr/libexec/anduinos-control-panel/software-source-helper"
SOURCE_PATH = Path("/etc/apt/sources.list.d/ubuntu.sources")
OS_RELEASE = Path("/etc/os-release")
gettext.bindtextdomain(TEXT_DOMAIN, LOCALE_DIR)
_ = lambda message: gettext.dgettext(TEXT_DOMAIN, message)

FLATHUB_REMOTE = "flathub"
FLATHUB_OFFICIAL_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
FLATHUB_SOURCES = {
    "official": "https://dl.flathub.org/repo",
    "sjtu": "https://mirror.sjtu.edu.cn/flathub",
    "ustc": "https://mirrors.ustc.edu.cn/flathub",
}


def current_flathub(runner=None) -> str:
    """Read only the default system installation, including disabled remotes."""
    result = (runner or subprocess.run)(
        ["/usr/bin/flatpak", "remotes", "--system", "--show-disabled", "--columns=name,url"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not read system Flathub")
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if fields[0] == FLATHUB_REMOTE:
            return fields[1].strip() if len(fields) > 1 else ""
    return ""


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


def system_codename(path: Path = OS_RELEASE) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(
        r'(?m)^VERSION_CODENAME=["\']?([a-z0-9][a-z0-9-]*)["\']?$', content
    )
    if not match:
        raise RuntimeError("System VERSION_CODENAME is missing or invalid")
    return match.group(1)


def system_architecture(machine: str | None = None) -> str:
    value = (machine or platform.machine()).casefold()
    if value in {"x86_64", "amd64"}:
        return "amd64"
    if value in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported architecture: {value or 'unknown'}")


def current_mirror(path: Path = SOURCE_PATH) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    match = re.search(r"(?m)^\s*URIs:\s*(\S+)", content)
    return match.group(1) if match else ""


def validate_mirror_uri(uri: str) -> str:
    """Accept a single HTTP(S) Ubuntu archive base URL, without URL tricks."""
    uri = uri.strip()
    if not uri or any(ord(char) < 33 or ord(char) == 127 for char in uri):
        raise ValueError("Enter one HTTP or HTTPS mirror address")
    if any(char in uri for char in "\\%?#"):
        raise ValueError("Mirror address contains an unsupported character")
    try:
        parsed = urllib.parse.urlsplit(uri)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Mirror address has an invalid port") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
        or (port is not None and port == 0)
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("Enter an HTTP or HTTPS Ubuntu mirror address")
    return uri.rstrip("/") + "/"


def probe_mirror(
    uri: str,
    codename: str,
    architecture: str,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> None:
    """Check that a custom mirror contains this release and architecture."""
    uri = validate_mirror_uri(uri)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", codename):
        raise ValueError("Invalid Ubuntu release")
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("Unsupported architecture")
    for path in (
        f"dists/{codename}/Release",
        f"dists/{codename}/main/binary-{architecture}/Packages.gz",
    ):
        response = opener(urllib.request.Request(uri + path), timeout=8)
        try:
            if getattr(response, "status", 200) != 200 or not response.read(1):
                raise RuntimeError("Mirror lacks the current Ubuntu release or architecture")
        finally:
            response.close()


def replace_ubuntu_uris(content: str, mirror: str) -> str:
    mirror = validate_mirror_uri(mirror)
    updated, count = re.subn(
        r"(?m)^(\s*URIs:\s*).+$",
        lambda match: match.group(1) + mirror,
        content,
    )
    if count == 0:
        raise ValueError("Ubuntu Deb822 source contains no URIs field")
    return updated


def measure_mirrors(
    codename: str,
    architecture: str,
    *,
    candidates: tuple[str, ...] = MIRRORS,
    opener: Callable[..., object] = urllib.request.urlopen,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str, str, float], None] | None = None,
) -> tuple[MirrorMeasurement, ...]:
    """Probe latency concurrently, then bandwidth-test the best five."""

    if architecture not in {"amd64", "arm64"}:
        raise ValueError("architecture must be amd64 or arm64")

    def latency(uri: str) -> tuple[str, float | None]:
        url = f"{uri}dists/{codename}/Release"
        # Some valid mirrors serve Release with GET but reject HEAD. Read only
        # one byte in the fallback so a mirror's metadata is not downloaded.
        for method, timeout in (("HEAD", 3), ("GET", 5)):
            headers = {"Range": "bytes=0-0"} if method == "GET" else {}
            request = urllib.request.Request(url, headers=headers, method=method)
            started = clock()
            try:
                response = opener(request, timeout=timeout)
                try:
                    status = getattr(response, "status", 200)
                    if status in ({200} if method == "HEAD" else {200, 206}):
                        if method == "HEAD" or response.read(1):
                            return uri, (clock() - started) * 1000
                finally:
                    response.close()
            except urllib.error.HTTPError as error:
                error.close()
                continue
            except Exception:
                continue
        return uri, None

    reachable: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(latency, uri) for uri in candidates]
        for future in as_completed(futures):
            uri, elapsed = future.result()
            if elapsed is not None:
                reachable.append((uri, elapsed))
                if progress is not None:
                    progress("latency", uri, elapsed)
    if not reachable:
        raise RuntimeError("No Ubuntu archive mirror is reachable")

    reachable.sort(key=lambda item: (item[1], not item[0].startswith("https://")))
    finalists = reachable[:5]

    def bandwidth(uri: str) -> tuple[str, float]:
        urls = (
            f"{uri}dists/{codename}/main/binary-{architecture}/Packages.gz",
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
        futures = [executor.submit(bandwidth, uri) for uri, _elapsed in finalists]
        for future in as_completed(futures):
            uri, speed = future.result()
            speeds[uri] = speed
            if progress is not None:
                progress("bandwidth", uri, speed)

    latency_by_uri = dict(finalists)
    ranked = sorted(
        finalists,
        key=lambda item: (
            -speeds.get(item[0], 0.0),
            latency_by_uri[item[0]],
            not item[0].startswith("https://"),
        ),
    )
    ranked += sorted(
        (item for item in reachable if item[0] not in latency_by_uri),
        key=lambda item: item[1],
    )
    return tuple(
        MirrorMeasurement(uri, elapsed, speeds.get(uri, 0.0))
        for uri, elapsed in ranked
    )


def select_fastest_mirror(*args, **kwargs) -> MirrorMeasurement:
    return measure_mirrors(*args, **kwargs)[0]


def validate_flathub_uri(uri: str) -> str:
    """Use the same HTTP(S) address rules as APT, without its trailing slash."""
    return validate_mirror_uri(uri).rstrip("/")


def _flathub_bytes(uri, path, *, opener, limit=1024 * 1024, checksum=None):
    response = opener(urllib.request.Request(f"{uri}/{path}"), timeout=8)
    try:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError("Flathub metadata is unavailable")
        data = response.read(limit + 1)
    finally:
        response.close()
    if not data or len(data) > limit:
        raise RuntimeError("Flathub metadata has an invalid size")
    if checksum and hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError("Flathub metadata checksum does not match")
    return data


def _flathub_variant(data, signature):
    try:
        variant = GLib.Variant.new_from_bytes(
            GLib.VariantType.new(signature), GLib.Bytes.new(data), False,
        )
    except GLib.Error as error:
        raise ValueError("Invalid Flathub metadata") from error
    if not variant.is_normal_form():
        raise ValueError("Invalid Flathub metadata")
    return variant.unpack()


def probe_flathub(uri, architecture=None, opener=urllib.request.urlopen):
    """Validate an actual Flathub repository and its current-architecture index."""
    uri = validate_flathub_uri(uri)
    architecture = architecture or system_architecture()
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("Unsupported Flatpak architecture")
    arch = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(_flathub_bytes(uri, "config", opener=opener, limit=65536).decode("utf-8"))
    if config.get("core", "collection-id", fallback="") != "org.flathub.Stable":
        raise ValueError("This address is not a Flathub mirror")
    index, _metadata = _flathub_variant(
        _flathub_bytes(uri, "summary.idx", opener=opener),
        "(a{s(ayaaya{sv})}a{sv})",
    )
    if arch not in index or len(index[arch][0]) != 32:
        raise ValueError("Flathub mirror lacks the current architecture")
    return bytes(index[arch][0]).hex()


def _flathub_sample(uri, summary_checksum, architecture, opener):
    """Resolve current AppStream data, rather than pinning a version or object."""
    compressed = _flathub_bytes(
        uri, f"summaries/{summary_checksum}.gz", opener=opener, limit=8 * 1024 * 1024,
    )
    limit = 16 * 1024 * 1024
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit or hashlib.sha256(data).hexdigest() != summary_checksum:
        raise ValueError("Invalid Flathub summary checksum or size")
    refs, _metadata = _flathub_variant(data, "(a(s(taya{sv}))a{sv})")
    arch = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    checksum = next((bytes(value[1]).hex() for ref, value in refs
                     if ref == f"appstream2/{arch}"), None)
    if not checksum or len(checksum) != 64:
        raise ValueError("Flathub AppStream reference is missing")

    def object_data(checksum, kind, signature):
        data = _flathub_bytes(
            uri, f"objects/{checksum[:2]}/{checksum[2:]}.{kind}",
            opener=opener, checksum=checksum,
        )
        return _flathub_variant(data, signature)

    commit = object_data(checksum, "commit", "(a{sv}aya(say)sstayay)")
    checksum = bytes(commit[6]).hex()
    if len(checksum) != 64:
        raise ValueError("Invalid Flathub root tree")
    files, _directories = object_data(checksum, "dirtree", "(a(say)a(sayay))")
    checksum = next((bytes(value).hex() for name, value in files
                     if name in {"appstream.xml", "appstream.xml.gz"}), None)
    if not checksum or len(checksum) != 64:
        raise ValueError("Flathub AppStream data is missing")
    return f"objects/{checksum[:2]}/{checksum[2:]}.filez"


def measure_flathub_mirrors(
    architecture, *, candidates=None, opener=urllib.request.urlopen,
    clock=time.monotonic, progress=None,
):
    """Measure reachable repositories against one dynamically resolved object.

    Metadata selects a download sample only; Flatpak still verifies official
    signatures when installing. No remote configuration or trusted keys change.
    """
    candidates = tuple(dict.fromkeys(validate_flathub_uri(uri) for uri in (
        FLATHUB_SOURCES.values() if candidates is None else candidates
    )))

    def latency(uri):
        started = clock()
        try:
            checksum = probe_flathub(uri, architecture, opener)
            elapsed = (clock() - started) * 1000
            if progress:
                progress("latency", uri, elapsed)
            return uri, elapsed, checksum
        except (OSError, ValueError, RuntimeError, GLib.Error, configparser.Error):
            return None

    with ThreadPoolExecutor(max_workers=3) as executor:
        reachable = [item for item in executor.map(latency, candidates) if item]
    reachable.sort(key=lambda item: item[1])
    sample = None
    for uri, _elapsed, checksum in reachable:
        try:
            sample = _flathub_sample(uri, checksum, architecture, opener)
            break
        except (OSError, EOFError, ValueError, RuntimeError, GLib.Error, zlib.error):
            continue
    if sample is None:
        raise RuntimeError("No usable Flathub download sample is reachable")

    def bandwidth(item):
        uri, elapsed, _checksum = item
        started = clock()
        response = None
        try:
            response = opener(urllib.request.Request(f"{uri}/{sample}"), timeout=5)
            if getattr(response, "status", 200) != 200:
                return None
            headers = getattr(response, "headers", {})
            if "text/html" in headers.get("Content-Type", ""):
                return None
            size = 0
            while clock() - started < 3.0 and size < 32 * 1024 * 1024:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
            duration = clock() - started
            if not size or duration <= 0:
                return None
            speed = size * 8 / duration / 1024 / 1024
            if progress:
                progress("bandwidth", uri, speed)
            return MirrorMeasurement(uri, elapsed, speed)
        except (OSError, ValueError):
            return None
        finally:
            if response is not None:
                response.close()

    with ThreadPoolExecutor(max_workers=3) as executor:
        measured = [item for item in executor.map(bandwidth, reachable) if item]
    if not measured:
        raise RuntimeError("No Flathub mirror could download the sample")
    return tuple(sorted(measured, key=lambda item: (-item.bandwidth_mbps, item.latency_ms)))


def simulated_upgrade_count(output: str) -> int:
    match = re.search(r"(?m)^(\d+) upgraded,", output)
    if not match:
        raise ValueError("Could not read the package update summary")
    return int(match.group(1))


def _icon_path(name="yast-upgrade.svg") -> Path:
    installed = Path("/usr/share/anduinos-control-panel/icons") / name
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[2] / "resources/icons" / name


def _source_overview(icon_name, current):
    overview = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    icon = Gtk.Image.new_from_file(str(_icon_path(icon_name)))
    icon.set_pixel_size(64)
    icon.set_valign(Gtk.Align.CENTER)
    overview.append(icon)
    source_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    source_info.set_valign(Gtk.Align.CENTER)
    source_info.set_hexpand(True)
    title = Gtk.Label(label=_("Current mirror"), xalign=0)
    title.add_css_class("heading")
    source_info.append(title)
    address = Gtk.Label(label=current, wrap=True, halign=Gtk.Align.START, xalign=0)
    address.add_css_class("dim-label")
    source_info.append(address)
    overview.append(source_info)
    return overview, title, address


class SoftwareSourceWindow(Adw.Window):
    """In-process Libadwaita window migrated from the OOBE update page."""

    def __init__(self, owner):
        super().__init__(
            transient_for=owner,
            modal=True,
            title=_("Software Source"),
            default_width=910,
            default_height=460,
        )
        self._busy = False
        self._updates_available = False
        self._flathub_loaded = False
        self._measurements: dict[str, MirrorMeasurement] = {}
        self._flathub_measurements: dict[str, MirrorMeasurement] = {}

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_content(toolbar)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroll.set_overlay_scrolling(False)
        toolbar.set_content(scroll)

        clamp = Adw.Clamp(maximum_size=720)
        scroll.set_child(clamp)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_top(28)
        body.set_margin_bottom(28)
        body.set_margin_start(24)
        body.set_margin_end(24)
        body.set_valign(Gtk.Align.START)
        clamp.set_child(body)

        current = current_mirror()
        self.source_overview, self.current_source_title, self.current_source = _source_overview(
            "yast-upgrade.svg", current,
        )
        body.append(self.source_overview)
        self.flathub_overview, self.flathub_source_title, self.flathub_current = _source_overview(
            "gnome-software.svg", "…",
        )
        self.flathub_overview.set_visible(False)
        body.append(self.flathub_overview)

        self.pages = Adw.ViewStack()
        switcher = Adw.ViewSwitcherTitle(stack=self.pages, title=_("Software Source"))
        header.set_title_widget(switcher)
        self.source_switcher = switcher
        switcher_bar = Adw.ViewSwitcherBar(stack=self.pages)
        switcher.bind_property(
            "title-visible", switcher_bar, "reveal", GObject.BindingFlags.SYNC_CREATE,
        )
        toolbar.add_bottom_bar(switcher_bar)
        self.source_switcher_bar = switcher_bar
        body.append(self.pages)

        automatic = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.pages.add_titled_with_icon(
            automatic, "automatic", "APT · " + _("Automatic"), "network-transmit-receive-symbolic",
        )
        source_group = Adw.PreferencesGroup()
        source_row = Adw.ActionRow(title=_("Software Source"))
        self.mirror_button = Gtk.Button(label=_("  Switch to Fastest Mirror  ").strip())
        self.mirror_button.add_css_class("suggested-action")
        self.mirror_button.set_valign(Gtk.Align.CENTER)
        self.mirror_button.connect("clicked", self._find_mirror)
        source_row.add_suffix(self.mirror_button)
        source_group.add(source_row)
        automatic.append(source_group)

        update_group = Adw.PreferencesGroup()
        update_row = Adw.ActionRow(
            title=_("Keep Your System Up to Date"),
            subtitle=_("Security patches and the latest features are just a click away."),
        )
        self.update_button = Gtk.Button(label=_("  Check for Updates  ").strip())
        self.update_button.set_valign(Gtk.Align.CENTER)
        self.update_button.connect("clicked", self._update_action)
        update_row.add_suffix(self.update_button)
        update_group.add(update_row)
        automatic.append(update_group)

        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.pages.add_titled_with_icon(
            advanced, "advanced", "APT · " + _("Advanced"), "preferences-system-symbolic",
        )
        custom_current = bool(current and current not in MIRRORS)
        self._mirror_options = (
            MIRRORS if custom_current
            else tuple(dict.fromkeys((current, *MIRRORS))) if current else MIRRORS
        )
        self.mirror_dropdown = Gtk.DropDown.new_from_strings(
            [_("Custom address…"), *self._mirror_options]
        )
        self.mirror_dropdown.connect("notify::selected", self._mirror_selection_changed)
        advanced.append(self.mirror_dropdown)
        self.custom_entry = Gtk.Entry(
            placeholder_text="https://mirror.example/ubuntu/",
            visible=custom_current,
        )
        self.custom_entry.set_text(current if custom_current else "")
        advanced.append(self.custom_entry)
        self.mirror_dropdown.set_selected(0 if custom_current else 1)
        advanced_buttons = Gtk.Box(spacing=12)
        advanced_buttons.set_halign(Gtk.Align.END)
        self.test_button = Gtk.Button(label=_("Test mirror speeds"))
        self.test_button.connect("clicked", self._test_mirrors)
        advanced_buttons.append(self.test_button)
        self.apply_button = Gtk.Button(label=_("Apply selected mirror"))
        self.apply_button.connect("clicked", self._apply_selected_mirror)
        advanced_buttons.append(self.apply_button)
        advanced.append(advanced_buttons)

        flatpak_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.pages.add_titled_with_icon(
            flatpak_page, "flatpak", "Flathub · " + _("Automatic"), "network-transmit-receive-symbolic",
        )
        flathub_group = Adw.PreferencesGroup()
        flathub_row = Adw.ActionRow(title=_("Flathub software source"))
        flathub_group.add(flathub_row)
        flatpak_page.append(flathub_group)
        self.flathub_fastest = Gtk.Button(label=_("  Switch to Fastest Mirror  ").strip())
        self.flathub_fastest.add_css_class("suggested-action")
        self.flathub_fastest.set_valign(Gtk.Align.CENTER)
        self.flathub_fastest.connect("clicked", lambda _button: self._measure_flathub(automatic=True))
        flathub_row.add_suffix(self.flathub_fastest)

        flatpak_advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.pages.add_titled_with_icon(
            flatpak_advanced, "flatpak-advanced", "Flathub · " + _("Advanced"), "preferences-system-symbolic",
        )
        self._flathub_options = tuple(FLATHUB_SOURCES.values())
        self.flathub_dropdown = Gtk.DropDown.new_from_strings(
            [_("Custom address…"), *self._flathub_labels()]
        )
        self.flathub_dropdown.connect("notify::selected", self._flathub_selection_changed)
        flatpak_advanced.append(self.flathub_dropdown)
        self.flathub_custom = Gtk.Entry(placeholder_text="https://mirror.example/flathub", visible=False)
        flatpak_advanced.append(self.flathub_custom)
        self.flathub_dropdown.set_selected(1)
        flathub_buttons = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        self.flathub_test = Gtk.Button(label=_("Test mirror speeds"))
        self.flathub_test.connect("clicked", lambda _button: self._measure_flathub(automatic=False))
        flathub_buttons.append(self.flathub_test)
        self.flathub_apply = Gtk.Button(label=_("Apply selected mirror"))
        self.flathub_apply.add_css_class("suggested-action")
        self.flathub_apply.connect("clicked", self._apply_flathub)
        flathub_buttons.append(self.flathub_apply)
        flatpak_advanced.append(flathub_buttons)

        self.output_expander = Gtk.Expander(label=_("Terminal Output"), visible=False)
        output_scroll = Gtk.ScrolledWindow(
            min_content_height=170,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        self.output = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        self.output.add_css_class("card")
        output_scroll.set_child(self.output)
        self.output_expander.set_child(output_scroll)

        self.status = Gtk.Label(wrap=True, xalign=0, visible=False)
        self.status.add_css_class("dim-label")
        body.append(self.status)

        self.progress = Gtk.ProgressBar(visible=False)
        body.append(self.progress)
        body.append(self.output_expander)

        self.pages.connect("notify::visible-child-name", self._source_page_changed)

    def _source_page_changed(self, _stack, _property) -> None:
        flatpak_page = self.pages.get_visible_child_name().startswith("flatpak")
        self.source_overview.set_visible(not flatpak_page)
        self.flathub_overview.set_visible(flatpak_page)
        if not self._busy:
            self.status.set_label("")
            self.status.set_visible(False)
            self.output_expander.set_visible(False)
        if flatpak_page and not self._flathub_loaded and not self._busy:
            self._set_busy(True)
            threading.Thread(target=self._read_flathub_worker, daemon=True).start()

    def _read_flathub_worker(self) -> None:
        try:
            uri = current_flathub()
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            GLib.idle_add(self._append_output, f"{error}\n")
            GLib.idle_add(self._flathub_read_failed)
        else:
            GLib.idle_add(self._flathub_read_complete, uri)

    def _flathub_read_failed(self) -> bool:
        self.flathub_current.set_label(_("Could not read the Flathub source."))
        self.output_expander.set_visible(True)
        return self._finish(_("✗ Operation failed: ") + _("Review Terminal Output for details."))

    def _flathub_read_complete(self, uri: str, message: str = "") -> bool:
        self._flathub_loaded = True
        self.flathub_current.set_label(uri or _("Flathub is not configured."))
        known = uri.rstrip("/") in self._flathub_options
        self.flathub_custom.set_text(uri if uri and not known else "")
        self.flathub_dropdown.set_selected(
            self._flathub_options.index(uri.rstrip("/")) + 1 if known else 0 if uri else 1
        )
        return self._finish(message)

    def _flathub_labels(self):
        names = [_("Official Flathub"), "SJTU", _("USTC (China)")]
        for index, uri in enumerate(FLATHUB_SOURCES.values()):
            item = self._flathub_measurements.get(uri)
            if item:
                names[index] += f" — {item.latency_ms:.0f} ms · {item.bandwidth_mbps:.1f} Mbps"
        return names

    def _flathub_selection_changed(self, _dropdown, _property) -> None:
        if hasattr(self, "flathub_custom"):
            self.flathub_custom.set_visible(self.flathub_dropdown.get_selected() == 0)
        if hasattr(self, "flathub_apply"):
            self.flathub_apply.set_sensitive(not self._busy)

    def _selected_flathub_source(self) -> str:
        index = self.flathub_dropdown.get_selected()
        if index == 0:
            return validate_flathub_uri(self.flathub_custom.get_text())
        if index > len(self._flathub_options):
            raise ValueError("No Flathub source selected")
        return self._flathub_options[index - 1]

    def _apply_flathub(self, _button) -> None:
        if self._busy:
            return
        try:
            source = self._selected_flathub_source()
        except ValueError:
            self._finish(_("Enter a valid HTTP or HTTPS mirror address."))
            return
        self._confirm_flathub(self._flathub_measurements.get(source, source))

    def _measure_flathub(self, *, automatic):
        if self._busy:
            return
        try:
            selected = None if automatic else self._selected_flathub_source()
        except ValueError:
            self._finish(_("Enter a valid HTTP or HTTPS mirror address."))
            return
        self._set_busy(True)
        self.status.set_label(_("Testing mirrors…"))
        self.output.get_buffer().set_text("")
        self.output_expander.set_visible(True)
        self.output_expander.set_expanded(True)

        def progress(kind, uri, value):
            unit = "ms" if kind == "latency" else "Mbps"
            GLib.idle_add(self._append_output, f"  {uri} — {value:.1f} {unit}\n")

        def worker():
            try:
                measurements = measure_flathub_mirrors(
                    system_architecture(), candidates=(selected,) if selected else None,
                    progress=progress,
                )
            except (OSError, ValueError, RuntimeError) as error:
                GLib.idle_add(self._append_output, f"{error}\n")
                GLib.idle_add(self._finish, _("✗ Mirror test failed: ") + _("Review Terminal Output for details."))
                return
            GLib.idle_add(self._flathub_measured, measurements, automatic)

        threading.Thread(target=worker, daemon=True).start()

    def _flathub_measured(self, measurements, automatic):
        self._flathub_measurements.update({item.uri: item for item in measurements})
        selected = self.flathub_dropdown.get_selected()
        self.flathub_dropdown.set_model(Gtk.StringList.new([_("Custom address…"), *self._flathub_labels()]))
        self.flathub_dropdown.set_selected(selected)
        item = measurements[0]
        self._finish(_("Mirror speed test complete.") + f" {item.uri} — {item.latency_ms:.0f} ms · {item.bandwidth_mbps:.1f} Mbps")
        if automatic:
            self._confirm_flathub(item)
        return GLib.SOURCE_REMOVE

    def _confirm_flathub(self, measurement):
        uri = measurement.uri if isinstance(measurement, MirrorMeasurement) else measurement
        details = (
            f"{_('Latency')}: {measurement.latency_ms:.0f} ms · {_('Bandwidth')}: {measurement.bandwidth_mbps:.1f} Mbps"
            if isinstance(measurement, MirrorMeasurement) else ""
        )
        # A configured URL may belong to a disabled remote. Reapplying it must
        # still reach the helper, which enables the selected system remote.
        dialog = Adw.MessageDialog(
            transient_for=self, heading=_("Apply selected mirror"),
            body=f"Flathub\n{uri}\n{details}",
        )
        dialog.add_response("keep", _("Keep Current"))
        dialog.add_response("switch", _("Switch"))
        dialog.set_close_response("keep")
        dialog.set_default_response("switch")
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)

        def response(_dialog, answer):
            if answer != "switch":
                self._finish("")
                return
            self._set_busy(True)
            self.status.set_label(_("Switching mirror…"))
            self.output_expander.set_visible(True)
            threading.Thread(target=self._flathub_worker, args=(uri,), daemon=True).start()

        dialog.connect("response", response)
        dialog.present()

    def _flathub_worker(self, source: str) -> None:
        code, output = self._run_helper("switch-flathub", source)
        if code:
            message = _("✗ Switch failed: ") + self._failure_message(code, output)
        else:
            message = _("Flathub source switched.")
        try:
            uri = current_flathub()
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            GLib.idle_add(self._append_output, f"{error}\n")
            GLib.idle_add(self._flathub_read_failed)
        else:
            GLib.idle_add(self._flathub_read_complete, uri, message)


    def _mirror_selection_changed(self, _dropdown, _property) -> None:
        self.custom_entry.set_visible(
            self.mirror_dropdown.get_selected() == 0
        )

    def _selected_mirror(self) -> str:
        selected = self.mirror_dropdown.get_selected()
        if selected == 0:
            return validate_mirror_uri(self.custom_entry.get_text())
        if selected > len(self._mirror_options):
            raise ValueError("No mirror selected")
        return self._mirror_options[selected - 1]

    def _set_mirror_measurements(self, measurements: tuple[MirrorMeasurement, ...]) -> bool:
        self._measurements.update({item.uri: item for item in measurements})
        selected = self.mirror_dropdown.get_selected()
        labels = []
        for uri in self._mirror_options:
            item = self._measurements.get(uri)
            if item is None:
                labels.append(uri)
            elif item.bandwidth_mbps:
                labels.append(f"{uri} — {item.latency_ms:.0f} ms · {item.bandwidth_mbps:.1f} Mbps")
            else:
                labels.append(f"{uri} — {item.latency_ms:.0f} ms")
        self.mirror_dropdown.set_model(Gtk.StringList.new([_("Custom address…"), *labels]))
        self.mirror_dropdown.set_selected(selected)
        self._mirror_selection_changed(None, None)
        message = _("Mirror speed test complete.")
        if len(measurements) == 1:
            item = measurements[0]
            message += f" {item.uri} — {item.latency_ms:.0f} ms · {item.bandwidth_mbps:.1f} Mbps"
        self._finish(message)
        return GLib.SOURCE_REMOVE

    def _append_output(self, text: str) -> bool:
        buffer = self.output.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.output.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.status.set_visible(True)
        self.mirror_button.set_sensitive(not busy)
        self.update_button.set_sensitive(not busy)
        self.mirror_dropdown.set_sensitive(not busy)
        self.custom_entry.set_sensitive(not busy)
        self.test_button.set_sensitive(not busy)
        self.apply_button.set_sensitive(not busy)
        self.source_switcher.set_sensitive(not busy)
        self.source_switcher_bar.set_sensitive(not busy)
        self.flathub_dropdown.set_sensitive(not busy)
        self.flathub_custom.set_sensitive(not busy)
        self.flathub_fastest.set_sensitive(not busy)
        self.flathub_test.set_sensitive(not busy)
        self._flathub_selection_changed(None, None)
        self.set_deletable(not busy)
        self.progress.set_visible(busy)
        if busy:
            self.progress.pulse()
            GLib.timeout_add(100, self._pulse)

    def _pulse(self) -> bool:
        if not self._busy:
            return GLib.SOURCE_REMOVE
        self.progress.pulse()
        return GLib.SOURCE_CONTINUE

    def _finish(self, message: str, select_current: bool = False) -> bool:
        self.status.set_label(message)
        self._set_busy(False)
        self.status.set_visible(bool(message))
        current = current_mirror()
        if current:
            self.current_source.set_label(current)
            if select_current:
                if current in self._mirror_options:
                    self.mirror_dropdown.set_selected(self._mirror_options.index(current) + 1)
                else:
                    self.custom_entry.set_text(current)
                    self.mirror_dropdown.set_selected(0)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _failure_message(return_code: int, output: str) -> str:
        if return_code in {126, 127}:
            return _("Authentication was cancelled.")
        if "original source was restored, but refreshing it also failed" in output:
            return _("The original source was restored, but refreshing it failed.")
        if "original source was restored" in output:
            return _("The original source was restored.")
        return _("Review Terminal Output for details.")

    def _run_helper(self, action: str, *arguments: str) -> tuple[int, str]:
        lines: list[str] = []
        try:
            process = subprocess.Popen(
                ["/usr/bin/pkexec", HELPER, action, *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if process.stdout is not None:
                for line in process.stdout:
                    lines.append(line)
                    GLib.idle_add(self._append_output, line)
            return process.wait(), "".join(lines)
        except OSError as error:
            GLib.idle_add(self._append_output, f"{error}\n")
            return 1, str(error)

    def _find_mirror(self, _button) -> None:
        self._measure_mirrors(automatic=True)

    def _test_mirrors(self, _button) -> None:
        try:
            selected = self._selected_mirror()
        except ValueError:
            self._finish(_("Enter a valid HTTP or HTTPS mirror address."))
            return
        self._measure_mirrors(automatic=False, selected=selected)

    def _measure_mirrors(self, *, automatic: bool, selected: str | None = None) -> None:
        self._set_busy(True)
        self.output_expander.set_visible(True)
        self.output_expander.set_expanded(True)
        self.status.set_label(_("Testing mirrors…"))
        self.output.get_buffer().set_text("")
        self._append_output(_("=== Testing mirror speeds ===") + "\n")

        def progress(kind: str, uri: str, value: float) -> None:
            unit = "ms" if kind == "latency" else "Mbps"
            GLib.idle_add(self._append_output, f"  {uri} — {value:.1f} {unit}\n")

        def worker() -> None:
            try:
                if selected and selected not in MIRRORS:
                    probe_mirror(selected, system_codename(), system_architecture())
                measurements = measure_mirrors(
                    system_codename(), system_architecture(),
                    candidates=(selected,) if selected else MIRRORS,
                    progress=progress,
                )
            except (OSError, UnicodeError, ValueError, RuntimeError) as error:
                GLib.idle_add(self._append_output, f"{error}\n")
                detail = (
                    _("No Ubuntu archive mirror is reachable.")
                    if str(error) == "No Ubuntu archive mirror is reachable"
                    else _("Review Terminal Output for details.")
                )
                GLib.idle_add(
                    self._finish,
                    _("✗ Mirror test failed: ") + detail,
                )
                return
            GLib.idle_add(self._set_mirror_measurements, measurements)
            if automatic:
                GLib.idle_add(self._confirm_mirror, measurements[0])

        threading.Thread(target=worker, daemon=True).start()

    def _apply_selected_mirror(self, _button) -> None:
        try:
            mirror = self._selected_mirror()
        except ValueError:
            self._finish(_("Enter a valid HTTP or HTTPS mirror address."))
            return
        if self.mirror_dropdown.get_selected() != 0:
            self._confirm_mirror(self._measurements.get(mirror, mirror))
            return
        self._set_busy(True)
        self.status.set_label(_("Testing mirrors…"))

        def worker() -> None:
            try:
                probe_mirror(mirror, system_codename(), system_architecture())
            except (OSError, ValueError, RuntimeError):
                GLib.idle_add(
                    self._finish,
                    _("Could not validate this mirror for the current system."),
                )
                return
            GLib.idle_add(self._confirm_mirror, mirror)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_mirror(self, measurement: MirrorMeasurement | str) -> bool:
        mirror = measurement.uri if isinstance(measurement, MirrorMeasurement) else measurement
        current = current_mirror()
        details = (
            f"{_('Latency')}: {measurement.latency_ms:.0f} ms · "
            f"{_('Bandwidth')}: {measurement.bandwidth_mbps:.1f} Mbps"
            if isinstance(measurement, MirrorMeasurement) else ""
        )
        if current == mirror:
            self._append_output(f"\n{mirror}\n{details}\n")
            self._finish(_("This is already your current mirror."))
            return GLib.SOURCE_REMOVE

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Fastest Mirror Found") if details else _("Apply selected mirror"),
            body=(
                f"{mirror}\n{details}\n\n"
                + _("Switch to this mirror and run apt update?")
            ),
        )
        dialog.add_response("keep", _("Keep Current"))
        dialog.add_response("switch", _("Switch"))
        dialog.set_close_response("keep")
        dialog.set_default_response("switch")
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)

        def response(_dialog: Adw.MessageDialog, answer: str) -> None:
            if answer != "switch":
                self._finish("")
                return
            self.status.set_label(_("Switching mirror…"))
            self.output_expander.set_visible(True)
            self.output_expander.set_expanded(True)
            threading.Thread(
                target=self._switch_worker,
                args=(mirror,),
                daemon=True,
            ).start()

        dialog.connect("response", response)
        dialog.present()
        return GLib.SOURCE_REMOVE

    def _switch_worker(self, mirror: str) -> None:
        code, output = self._run_helper("switch-mirror", mirror)
        if code == 0:
            self._updates_available = False
            GLib.idle_add(self.update_button.set_label, _("  Check for Updates  ").strip())
            message = _("✓ Mirror switched and system updated.")
        else:
            message = _("✗ Switch failed: ") + self._failure_message(code, output)
        GLib.idle_add(self._finish, message, code == 0)

    def _update_action(self, _button) -> None:
        self._set_busy(True)
        self.output_expander.set_visible(True)
        self.output_expander.set_expanded(True)
        if self._updates_available:
            self.status.set_label(_("Installing updates…"))
            threading.Thread(target=self._install_updates, daemon=True).start()
        else:
            self.status.set_label(_("Checking for updates…"))
            threading.Thread(target=self._check_updates, daemon=True).start()

    def _check_updates(self) -> None:
        code, output = self._run_helper("refresh")
        if code != 0:
            GLib.idle_add(
                self._finish,
                _("✗ Check failed: ") + self._failure_message(code, output),
            )
            return
        try:
            environment = os.environ.copy()
            environment["LC_ALL"] = "C"
            result = subprocess.run(
                ["/usr/bin/apt-get", "--simulate", "upgrade"],
                capture_output=True,
                text=True,
                timeout=120,
                env=environment,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
            count = simulated_upgrade_count(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError) as error:
            GLib.idle_add(self._append_output, f"{error}\n")
            GLib.idle_add(
                self._finish,
                _("✗ Check failed: ") + _("Review Terminal Output for details."),
            )
            return
        GLib.idle_add(self._checked, count)

    def _checked(self, count: int) -> bool:
        self._updates_available = count > 0
        self.update_button.set_label(
            (_("  Install Updates  ") if count else _("  Check for Updates  ")).strip()
        )
        message = (
            _("Updates are available.")
            if count
            else _("✓ System is up to date.")
        )
        return self._finish(message)

    def _install_updates(self) -> None:
        code, output = self._run_helper("upgrade")
        if code == 0:
            self._updates_available = False
            GLib.idle_add(
                self.update_button.set_label, _("  Check for Updates  ").strip()
            )
            message = _("✓ Updates installed successfully!")
        else:
            message = _("✗ Installation failed: ") + self._failure_message(
                code, output
            )
        GLib.idle_add(self._finish, message)
