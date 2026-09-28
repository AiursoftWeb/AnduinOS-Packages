"""Software-source and system-update settings for AnduinOS Control Panel."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import gettext
import os
from pathlib import Path
import platform
import re
import subprocess
import threading
import time
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk


LOCALE_DIR = "/usr/share/locale"
TEXT_DOMAIN = "anduinos-control-panel"
HELPER = "/usr/libexec/anduinos-control-panel/software-source-helper"
SOURCE_PATH = Path("/etc/apt/sources.list.d/ubuntu.sources")
OS_RELEASE = Path("/etc/os-release")
gettext.bindtextdomain(TEXT_DOMAIN, LOCALE_DIR)
_ = lambda message: gettext.dgettext(TEXT_DOMAIN, message)

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


def replace_ubuntu_uris(content: str, mirror: str) -> str:
    if mirror not in MIRRORS:
        raise ValueError("Mirror is not in the AnduinOS allowlist")
    updated, count = re.subn(
        r"(?m)^(\s*URIs:\s*).+$",
        lambda match: match.group(1) + mirror,
        content,
    )
    if count == 0:
        raise ValueError("Ubuntu Deb822 source contains no URIs field")
    return updated


def select_fastest_mirror(
    codename: str,
    architecture: str,
    *,
    candidates: tuple[str, ...] = MIRRORS,
    opener: Callable[..., object] = urllib.request.urlopen,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str, str, float], None] | None = None,
) -> MirrorMeasurement:
    """Probe latency concurrently, then bandwidth-test the best five."""

    if architecture not in {"amd64", "arm64"}:
        raise ValueError("architecture must be amd64 or arm64")

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
    best = min(
        (uri for uri, _elapsed in finalists),
        key=lambda uri: (
            -speeds.get(uri, 0.0),
            latency_by_uri[uri],
            not uri.startswith("https://"),
        ),
    )
    return MirrorMeasurement(
        best, latency_by_uri[best], speeds.get(best, 0.0)
    )


def simulated_upgrade_count(output: str) -> int:
    match = re.search(r"(?m)^(\d+) upgraded,", output)
    if not match:
        raise ValueError("Could not read the package update summary")
    return int(match.group(1))


def _icon_path() -> Path:
    installed = Path("/usr/share/anduinos-control-panel/icons/yast-upgrade.svg")
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[2] / "resources/icons/yast-upgrade.svg"


class SoftwareSourceWindow(Adw.Window):
    """In-process Libadwaita window migrated from the OOBE update page."""

    def __init__(self, owner):
        super().__init__(
            transient_for=owner,
            modal=True,
            title=_("Software Source"),
            default_width=680,
            default_height=680,
        )
        self._busy = False
        self._updates_available = False

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroll.set_overlay_scrolling(False)
        toolbar.set_content(scroll)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(28)
        body.set_margin_bottom(28)
        body.set_margin_start(36)
        body.set_margin_end(36)
        body.set_valign(Gtk.Align.CENTER)
        body.set_vexpand(True)
        scroll.set_child(body)

        icon = Gtk.Image.new_from_file(str(_icon_path()))
        icon.set_pixel_size(72)
        icon.set_halign(Gtk.Align.CENTER)
        body.append(icon)

        title = Gtk.Label(label=_("Keep Your System Up to Date"))
        title.add_css_class("title-1")
        title.set_wrap(True)
        title.set_justify(Gtk.Justification.CENTER)
        body.append(title)

        subtitle = Gtk.Label(
            label=_("Security patches and the latest features are just a click away.")
        )
        subtitle.add_css_class("dim-label")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        body.append(subtitle)

        current = current_mirror()
        self.current_source = Gtk.Label(
            label=(
                _("Current mirror") + f": {current}"
                if current
                else ""
            )
        )
        self.current_source.add_css_class("dim-label")
        self.current_source.set_wrap(True)
        self.current_source.set_selectable(True)
        body.append(self.current_source)

        self.output_expander = Gtk.Expander(label=_("Terminal Output"))
        self.output_expander.set_margin_top(12)
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
        body.append(self.output_expander)

        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.status.add_css_class("dim-label")
        body.append(self.status)

        self.progress = Gtk.ProgressBar(visible=False)
        body.append(self.progress)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.set_margin_top(8)
        self.mirror_button = Gtk.Button(label=_("  Switch to Fastest Mirror  "))
        self.mirror_button.connect("clicked", self._find_mirror)
        buttons.append(self.mirror_button)
        self.update_button = Gtk.Button(label=_("  Check for Updates  "))
        self.update_button.connect("clicked", self._update_action)
        buttons.append(self.update_button)
        body.append(buttons)

    def _append_output(self, text: str) -> bool:
        buffer = self.output.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.output.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.mirror_button.set_sensitive(not busy)
        self.update_button.set_sensitive(not busy)
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

    def _finish(self, message: str) -> bool:
        self.status.set_label(message)
        self._set_busy(False)
        current = current_mirror()
        if current:
            self.current_source.set_label(
                _("Current mirror") + f": {current}"
            )
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _failure_message(return_code: int, output: str) -> str:
        if return_code in {126, 127}:
            return _("Authentication was cancelled.")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return (
            lines[-1]
            if lines
            else _("The operation failed without an error message.")
        )

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
            return 1, str(error)

    def _find_mirror(self, _button) -> None:
        self._set_busy(True)
        self.output_expander.set_expanded(True)
        self.status.set_label(_("Testing mirrors…"))
        self._append_output(_("=== Testing mirror speeds ===") + "\n")

        def progress(kind: str, uri: str, value: float) -> None:
            unit = "ms" if kind == "latency" else "Mbps"
            GLib.idle_add(self._append_output, f"  {uri} — {value:.1f} {unit}\n")

        def worker() -> None:
            try:
                measurement = select_fastest_mirror(
                    system_codename(), system_architecture(), progress=progress
                )
            except (OSError, UnicodeError, ValueError, RuntimeError) as error:
                GLib.idle_add(
                    self._finish,
                    _("✗ Mirror test failed: ") + str(error),
                )
                return
            GLib.idle_add(self._confirm_mirror, measurement)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_mirror(self, measurement: MirrorMeasurement) -> bool:
        current = current_mirror()
        details = (
            f"{_('Latency')}: {measurement.latency_ms:.0f} ms · "
            f"{_('Bandwidth')}: {measurement.bandwidth_mbps:.1f} Mbps"
        )
        if current == measurement.uri:
            self._append_output(f"\n{measurement.uri}\n{details}\n")
            self._finish(_("This is already your current mirror."))
            return GLib.SOURCE_REMOVE

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Fastest Mirror Found"),
            body=(
                f"{measurement.uri}\n{details}\n\n"
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
            threading.Thread(
                target=self._switch_worker,
                args=(measurement.uri,),
                daemon=True,
            ).start()

        dialog.connect("response", response)
        dialog.present()
        return GLib.SOURCE_REMOVE

    def _switch_worker(self, mirror: str) -> None:
        code, output = self._run_helper("switch-mirror", mirror)
        if code == 0:
            message = _("✓ Mirror switched and system updated.")
        else:
            message = _("✗ Switch failed: ") + self._failure_message(code, output)
        GLib.idle_add(self._finish, message)

    def _update_action(self, _button) -> None:
        self._set_busy(True)
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
            GLib.idle_add(
                self._finish,
                _("✗ Check failed: ") + str(error),
            )
            return
        GLib.idle_add(self._checked, count)

    def _checked(self, count: int) -> bool:
        self._updates_available = count > 0
        self.update_button.set_label(
            _("  Install Updates  ") if count else _("  Check for Updates  ")
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
                self.update_button.set_label, _("  Check for Updates  ")
            )
            message = _("✓ Updates installed successfully!")
        else:
            message = _("✗ Installation failed: ") + self._failure_message(
                code, output
            )
        GLib.idle_add(self._finish, message)
