"""Wizard pages for the AnduinOS GTK4 installer.

Each page is built by a function that returns an Adw.NavigationPage.
Pages communicate through a shared state dict.

Navigation: each page gets a reference to the Adw.NavigationView so it
can push the next page when the user clicks "Next" / "Install".
"""

import threading
import re
import html
import subprocess

# Allow absolute imports when run directly (not as a package).
import sys, os
_install_dir = os.path.dirname(os.path.abspath(__file__))
if _install_dir not in sys.path:
    sys.path.insert(0, _install_dir)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio, Pango, GObject

from languages import (
    DEFAULT_LANGUAGE,
    KEYBOARD_LAYOUTS,
    LANGUAGES,
    RTL_LANGUAGES,
    default_timezone,
    input_method,
    language_for_locale,
    Language as LangData,
)
from i18n import _, N_
from frontend import (
    DevelopmentExecutorClient,
    ExecutorClient,
    StorageStrategy,
    apply_storage_strategy,
    bind_storage_target,
    clear_guided_storage_selection,
    clear_storage_target,
    create_install_plan,
    probe_storage_inventory,
)
from installer_core.btrfs import BTRFS_SUBVOLUMES
from installer_core.account_security import AccountNextAction, account_next_action
from installer_core.coexistence import CoexistenceNoticeCode
from installer_core.layout import MIB, build_erase_disk_layout_spec
from installer_core.model import (
    Filesystem,
    InstallMode,
    InstallPlan,
    SecureBoot,
)
from installer_core.probe import ProbeError, probe_platform
from installer_core.storage_ui import (
    GuidedStoragePreview,
    GuidedStorageSelection,
    StorageDiskChoice,
    StorageWorkflow,
    build_guided_storage_preview,
    build_guided_storage_confirmation,
    build_storage_workflow,
)
from installer_core.swap_policy import (
    calculate_swap_sizing,
    probe_physical_memory_bytes,
)
from installer_core.username_policy import (
    is_valid_username,
)
from installer_core.usernames import (
    suggest_username,
)
from installer_core.wifi import (
    WifiNetwork,
    scan_wifi_networks,
    set_wifi_radio,
    wifi_radio_enabled,
)
from slideshow import load_slides
from ui import card, clamp_content, icon_picture, page_hero


_COEXISTENCE_NOTICE_MESSAGES = {
    CoexistenceNoticeCode.UEFI_GPT_REQUIRED: N_(
        "Guided coexistence requires a system booted in UEFI mode and a "
        "GPT target disk. This disk cannot continue in guided mode."
    ),
    CoexistenceNoticeCode.GEOMETRY_UNAVAILABLE: N_(
        "The complete partition map and free-space geometry could not be "
        "read consistently. No space on this disk is authorized for "
        "installation."
    ),
    CoexistenceNoticeCode.IDENTITY_UNAVAILABLE: N_(
        "The GPT disk or one of its partitions has no unique stable "
        "identifier. Guided coexistence cannot safely authorize "
        "preservation or writes on this disk."
    ),
    CoexistenceNoticeCode.MAPPING_UNSUPPORTED: N_(
        "This disk contains an active mapper, array or other nested "
        "block-device topology that guided coexistence does not support."
    ),
    CoexistenceNoticeCode.UNMOUNT_AND_RESCAN: N_(
        "A partition on this disk is mounted. Unmount it and rescan storage "
        "before continuing."
    ),
    CoexistenceNoticeCode.USES_UNALLOCATED_SPACE_ONLY: N_(
        "AnduinOS will use only the selected unallocated space. The "
        "installer will not shrink or move an existing filesystem."
    ),
    CoexistenceNoticeCode.PRESERVES_EXISTING_PARTITIONS: N_(
        "Existing Windows, recovery and data partitions remain outside the "
        "write set and will be preserved."
    ),
    CoexistenceNoticeCode.ESP_REQUIRES_VALIDATION: N_(
        "The existing EFI System Partition is only a candidate. Health and "
        "free-space checks must pass before it can be reused, and it will "
        "never be formatted."
    ),
    CoexistenceNoticeCode.BITLOCKER_NOT_MODIFIED: N_(
        "BitLocker storage will be preserved. The installer will not "
        "unlock, resize, repair or otherwise modify it."
    ),
    CoexistenceNoticeCode.WINDOWS_STATE_NOT_REPAIRED: N_(
        "The installer will not mount, repair or infer the safety of Windows "
        "volumes from hibernation or Fast Startup state. Any required "
        "Windows maintenance must be completed in Windows."
    ),
    CoexistenceNoticeCode.DISPOSABLE_PARTITION_OPTION: N_(
        "Alternatively, you may explicitly select one entire partition to "
        "erase. Everything in that selected partition will be destroyed; it "
        "is never selected automatically."
    ),
    CoexistenceNoticeCode.NO_FORCE_CONTINUE: N_(
        "Installation cannot continue with this selection. There is no "
        "force-continue option around storage safety checks."
    ),
    CoexistenceNoticeCode.RESCAN_AFTER_CHANGES: N_(
        "After changing partitions or unmounting a volume, rescan storage "
        "and select the target again."
    ),
}

_SHRINK_IN_WINDOWS_MESSAGE = N_(
    "No suitable unallocated space was found. To protect your data, create "
    "unallocated space with Windows Disk Management, then boot the installer "
    "again and rescan the disk."
)
_SHRINK_WITH_PARTITION_TOOL_MESSAGE = N_(
    "No suitable unallocated space was found. To protect your data, create "
    "unallocated space with a partitioning tool, then boot the installer "
    "again and rescan the disk."
)

# GPT and partitioning tools deliberately leave tiny alignment gaps around
# real partitions. Keep their exact geometry in the safety snapshot, but do
# not render sub-4-MiB padding as if it were another layout item.
_LAYOUT_FREE_SPACE_MINIMUM_BYTES = 4 * 1024**2


def _coexistence_notice_text(notice, lang, windows_detected):
    if notice.code is CoexistenceNoticeCode.SHRINK_IN_WINDOWS:
        message = (
            _SHRINK_IN_WINDOWS_MESSAGE
            if windows_detected
            else _SHRINK_WITH_PARTITION_TOOL_MESSAGE
        )
    else:
        message = _COEXISTENCE_NOTICE_MESSAGES.get(
            notice.code,
            notice.message,
        )
    return _(message, lang)


# ── thin GObject wrapper for language list items ─────────────────────────
# Gio.ListStore requires GObject.Object items; Language is a plain dataclass.
# We wrap it so the list view can display language names via property binding.

class LanguageItem(GObject.Object):
    """GObject wrapper around a Language dataclass for use in ListStore."""
    __gtype_name__ = "LanguageItem"
    code = GObject.Property(type=str)
    native = GObject.Property(type=str)
    english = GObject.Property(type=str)

    def __init__(self, lang: LangData):
        super().__init__()
        self.code = lang.code
        self.native = lang.native_name
        self.english = lang.english_name
        self._lang = lang  # keep the original for lookups


# ── helpers ──────────────────────────────────────────────────────────────

def _nav_btn(label_key: str, lang: str, callback, sensitive: bool = True,
             css_classes: list[str] | None = None):
    """Create a labelled navigation button."""
    btn = Gtk.Button(label=_(label_key, lang), sensitive=sensitive)
    if css_classes:
        for c in css_classes:
            btn.add_css_class(c)
    btn.connect("clicked", lambda _b: callback())
    return btn


def _page_title(key: str, lang: str) -> Gtk.Label:
    """Big page title."""
    lbl = Gtk.Label(label=_(key, lang))
    lbl.add_css_class("title-1")
    lbl.set_halign(Gtk.Align.CENTER)
    lbl.set_margin_top(24)
    return lbl


def _page_subtitle(key: str, lang: str) -> Gtk.Label:
    """Smaller subtitle below the title."""
    lbl = Gtk.Label(label=_(key, lang))
    lbl.add_css_class("dim-label")
    lbl.set_halign(Gtk.Align.CENTER)
    lbl.set_margin_bottom(12)
    return lbl


def _nav_box(lang, on_back, on_next, next_label=N_("Next"),
             next_sensitive=True, next_destructive=False, stage=0,
             show_back=True):
    """Persistent-looking bottom bar with guarded navigation and progress."""
    box = Gtk.CenterBox()
    box.add_css_class("wizard-navigation")

    back = _nav_btn("Back", lang, on_back)
    back.set_visible(show_back)
    box.set_start_widget(back)

    dots = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=8,
        halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER,
    )
    dots.add_css_class("wizard-dots")
    for index in range(5):
        dot = Gtk.Box()
        dot.add_css_class("wizard-dot")
        if index < stage:
            dot.add_css_class("wizard-dot-complete")
        elif index == stage:
            dot.add_css_class("wizard-dot-active")
        dots.append(dot)
    box.set_center_widget(dots)

    css = ["destructive-action"] if next_destructive else ["suggested-action"]
    nxt = _nav_btn(next_label, lang, on_next,
                   sensitive=next_sensitive, css_classes=css)
    box.set_end_widget(nxt)
    box.back_button = back
    box.next_button = nxt
    return box


def _page_header(title, subtitle, icon, lang):
    return page_hero(_(title, lang), _(subtitle, lang), icon)


def internet_connection_ready(monitor=None) -> bool:
    """Return true only for a complete, non-portal Internet connection."""

    try:
        monitor = monitor or Gio.NetworkMonitor.get_default()
        return monitor.get_connectivity() == Gio.NetworkConnectivity.FULL
    except Exception:
        return False


def effective_network_choice(preferred: bool, online: bool) -> bool:
    """Enable a preferred optional download only while fully online."""

    return bool(preferred) and bool(online)


def should_show_network_page(shared, monitor=None) -> bool:
    """Keep the page visible for development or incomplete connectivity."""

    return bool(shared.get("development_mode")) or not internet_connection_ready(
        monitor
    )


def _recommended_input_method(shared):
    """Return the selected locale's maintained input-method policy, if any."""

    language = language_for_locale(str(shared.get("locale") or ""))
    return (
        input_method(language.recommended_input_method)
        if language is not None
        else None
    )


def _input_method_install_label(method, lang):
    """Describe the user capability before the implementation product."""

    return _(
        "Install {language} input method: {name}", lang
    ).format(
        language=method.language_name,
        name=method.display_name,
    )


def _offline_callout(lang):
    """Build the shared non-fatal offline warning used by optional downloads."""

    callout = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=12,
        margin_bottom=4,
    )
    callout.add_css_class("installer-warning-card")
    icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
    icon.set_pixel_size(24)
    icon.add_css_class("warning")
    callout.append(icon)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    heading = Gtk.Label(
        label=_("Connect to the Internet", lang),
        halign=Gtk.Align.START,
        xalign=0,
        wrap=True,
    )
    heading.add_css_class("heading")
    body = Gtk.Label(
        label=_(
            "Requires an Internet connection. The base installation "
            "remains available when offline.",
            lang,
        ),
        halign=Gtk.Align.START,
        xalign=0,
        wrap=True,
    )
    body.add_css_class("dim-label")
    text.append(heading)
    text.append(body)
    callout.append(text)
    return callout


def _list_item_row():
    """Return a neutral ListView child; Adw.ActionRow requires Gtk.ListBox."""

    row = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=2,
        margin_top=8,
        margin_bottom=8,
        margin_start=12,
        margin_end=12,
    )
    row.add_css_class("installer-list-row")
    title = Gtk.Label(
        halign=Gtk.Align.START,
        xalign=0,
        ellipsize=Pango.EllipsizeMode.END,
    )
    title.add_css_class("heading")
    subtitle = Gtk.Label(
        halign=Gtk.Align.START,
        xalign=0,
        ellipsize=Pango.EllipsizeMode.END,
    )
    subtitle.add_css_class("dim-label")
    row.append(title)
    row.append(subtitle)
    return row


def _bind_list_item_row(row, title, subtitle=""):
    title_label = row.get_first_child()
    subtitle_label = row.get_last_child()
    title_label.set_label(title)
    subtitle_label.set_label(subtitle)
    subtitle_label.set_visible(bool(subtitle))


# ── page 1: Welcome / Language selection ─────────────────────────────────

def build_welcome_page(shared, nav_view):
    """Language list on the left, native GTK4 welcome panel on the right."""
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("AnduinOS Installer", lang))
    page.set_tag("welcome")

    # ── left: language list ──
    list_store = Gio.ListStore(item_type=LanguageItem)
    lang_items = []  # keep parallel list for index-based lookup
    for l in LANGUAGES:
        item = LanguageItem(l)
        list_store.append(item)
        lang_items.append(l)

    factory = Gtk.SignalListItemFactory()
    def _on_setup(_f, item):
        item.set_child(_list_item_row())

    def _on_bind(_f, item):
        row = item.get_child()
        lang_item = item.get_item()
        _bind_list_item_row(row, lang_item.native, lang_item.english)

    factory.connect("setup", _on_setup)
    factory.connect("bind", _on_bind)

    lang_list = Gtk.ListView(model=Gtk.SingleSelection(model=list_store),
                             factory=factory)
    lang_list.set_vexpand(True)

    lang_scroll = Gtk.ScrolledWindow(min_content_width=300,
                                     hscrollbar_policy=Gtk.PolicyType.NEVER)
    lang_scroll.set_child(lang_list)
    lang_frame = Gtk.Frame()
    lang_frame.set_child(lang_scroll)
    lang_frame.add_css_class("installer-list-card")

    # ── right: native GTK4 welcome panel ──
    right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=24, vexpand=True, hexpand=True,
                        halign=Gtk.Align.CENTER,
                        valign=Gtk.Align.CENTER)

    # AnduinOS logo / icon
    welcome_icon = icon_picture("welcome", 160)
    right_box.append(welcome_icon)

    # Welcome text (changes with language selection)
    welcome_title = Gtk.Label()
    welcome_title.add_css_class("title-1")
    welcome_title.set_justify(Gtk.Justification.CENTER)

    welcome_desc = Gtk.Label()
    welcome_desc.add_css_class("dim-label")
    welcome_desc.set_justify(Gtk.Justification.CENTER)
    welcome_desc.set_wrap(True)
    welcome_desc.set_max_width_chars(40)

    right_box.append(welcome_title)
    right_box.append(welcome_desc)

    # ── layout ──
    hpaned = Gtk.Paned(
        orientation=Gtk.Orientation.HORIZONTAL,
        position=340,
        wide_handle=True,
        vexpand=True,
        margin_start=32,
        margin_end=32,
        margin_top=18,
        margin_bottom=12,
    )
    hpaned.set_start_child(lang_frame)
    hpaned.set_end_child(right_box)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    content.append(hpaned)

    # ── handlers ──
    sel = lang_list.get_model()
    navigation_widgets = {}

    def _update_welcome(lang_code: str):
        welcome_title.set_label(_("Welcome to AnduinOS", lang_code))
        welcome_desc.set_label(
            _("Choose your language to begin installation", lang_code)
        )
        page.set_title(_("AnduinOS Installer", lang_code))
        if navigation_widgets:
            navigation_widgets["back"].set_label(_("Back", lang_code))
            navigation_widgets["next"].set_label(_("Next", lang_code))

    def _on_lang_selected():
        pos = sel.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            l = lang_items[pos]
            shared["lang"] = l.code
            shared["locale"] = l.locale
            shared["keyboard"] = l.keyboard
            shared["timezone"] = default_timezone(l.code)
            recommends_input_method = l.recommended_input_method is not None
            shared["install_input_method"] = recommends_input_method
            shared["_preferred_install_input_method"] = (
                recommends_input_method
            )
            Gtk.Widget.set_default_direction(
                Gtk.TextDirection.RTL
                if l.code in RTL_LANGUAGES
                else Gtk.TextDirection.LTR
            )
            _update_welcome(l.code)
            set_window_language = shared.get("_set_window_language")
            if callable(set_window_language):
                set_window_language(l.code)

    sel.connect("selection-changed", lambda _s, _p, _n: _on_lang_selected())

    def on_next():
        try:
            next_page = (
                build_network_page(shared, nav_view)
                if should_show_network_page(shared)
                else build_keyboard_page(shared, nav_view)
            )
            nav_view.push(next_page)
        except Exception as e:
            import traceback
            traceback.print_exc()
            selected_lang = str(shared.get("lang", DEFAULT_LANGUAGE))
            dlg = Adw.MessageDialog(
                transient_for=nav_view.get_root(),
                heading=_("Navigation error", selected_lang),
                body=str(e),
            )
            dlg.add_response("ok", _("OK", selected_lang))
            dlg.present()

    navigation = _nav_box(
        lang,
        on_back=lambda: None,
        on_next=on_next,
        stage=0,
        show_back=False,
    )
    navigation_widgets["back"] = navigation.back_button
    navigation_widgets["next"] = navigation.next_button
    content.append(navigation)

    # Select the language detected from the Live session. The shared state is
    # initialized before this page is built, so regional defaults stay atomic.
    initial_language = str(shared.get("lang", DEFAULT_LANGUAGE))
    for i, l in enumerate(lang_items):
        if l.code == initial_language:
            lang_list.get_model().select_item(i, True)
            break
    _update_welcome(initial_language)

    page.set_child(content)
    return page


# ── page 2: Network recommendation ───────────────────────────────────────

def build_network_page(shared, nav_view):
    """Recommend connectivity while keeping offline installation available."""

    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Connect to the Internet", lang))
    page.set_tag("network")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Connect to the Internet",
            "Requires an Internet connection. The base installation remains available when offline.",
            "network",
            lang,
        )
    )

    body = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=18,
        homogeneous=True,
        margin_start=32,
        margin_end=32,
        margin_top=12,
        margin_bottom=8,
        vexpand=True,
    )

    explanation = card(spacing=14)
    explanation.set_vexpand(True)
    explanation_title = Gtk.Label(
        label=_("Updates and Drivers", lang),
        halign=Gtk.Align.START,
        xalign=0,
        wrap=True,
    )
    explanation_title.add_css_class("title-3")
    explanation.append(explanation_title)

    explanation_text = Gtk.Label(
        label=_(
            "Requires an Internet connection. The base installation "
            "remains available when offline.",
            lang,
        ),
        halign=Gtk.Align.START,
        xalign=0,
        wrap=True,
    )
    explanation_text.add_css_class("dim-label")
    explanation.append(explanation_text)

    online_features = [
        _("Download and install system updates during installation", lang),
        _("Install hardware drivers", lang),
    ]
    recommended_method = _recommended_input_method(shared)
    if recommended_method is not None:
        online_features.append(
            _input_method_install_label(recommended_method, lang)
        )
    for text in online_features:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
        row.append(
            Gtk.Label(
                label=text,
                halign=Gtk.Align.START,
                xalign=0,
                wrap=True,
            )
        )
        explanation.append(row)

    status_box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=10,
        halign=Gtk.Align.START,
        valign=Gtk.Align.END,
        vexpand=True,
    )
    status_icon = Gtk.Image(pixel_size=22)
    status_label = Gtk.Label(wrap=True, xalign=0)
    status_box.append(status_icon)
    status_box.append(status_label)
    explanation.append(status_box)

    body.append(explanation)

    networks = card(spacing=10)
    networks.set_vexpand(True)
    networks_header = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=10,
    )
    networks_title = Gtk.Label(
        label=_("Available Wi-Fi Networks", lang),
        halign=Gtk.Align.START,
        xalign=0,
        hexpand=True,
        wrap=True,
    )
    networks_title.add_css_class("title-3")
    radio_switch = Gtk.Switch(
        valign=Gtk.Align.CENTER,
        sensitive=False,
    )
    refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
    refresh_button.set_tooltip_text(_("Detect Internet connectivity", lang))
    networks_header.append(networks_title)
    networks_header.append(radio_switch)
    networks_header.append(refresh_button)
    networks.append(networks_header)

    wifi_group = Adw.PreferencesGroup()
    network_rows = []
    wifi_scroll = Gtk.ScrolledWindow(
        vexpand=True,
        min_content_height=220,
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
    )
    wifi_scroll.set_child(wifi_group)
    networks.append(wifi_scroll)
    body.append(networks)

    content.append(clamp_content(body, 920))
    monitor = Gio.NetworkMonitor.get_default()

    def _is_online():
        return internet_connection_ready(monitor)

    def _render_connectivity():
        online = _is_online()
        shared["network_preflight_online"] = online
        if online:
            status_box.remove_css_class("installer-warning-card")
            status_icon.remove_css_class("warning")
        else:
            status_box.add_css_class("installer-warning-card")
            status_icon.add_css_class("warning")
        status_icon.set_from_icon_name(
            "network-transmit-receive-symbolic"
            if online
            else "network-offline-symbolic"
        )
        status_label.set_label(
            _("Internet connection is ready.", lang)
            if online
            else _(
                "Requires an Internet connection. The base installation "
                "remains available when offline.",
                lang,
            )
        )

    def _open_wifi_settings():
        try:
            subprocess.Popen(("gnome-control-center", "wifi"))
        except OSError as error:
            status_label.set_label(
                _("Unavailable: {error}", lang).format(error=error)
            )

    def _replace_network_rows(networks: tuple[WifiNetwork, ...]):
        for row in network_rows:
            wifi_group.remove(row)
        network_rows.clear()
        for network in networks:
            detail = f"{network.signal}%"
            if network.security != "--":
                detail += f" · {network.security}"
            row = Adw.ActionRow(
                title=network.ssid,
                subtitle=detail,
                activatable=True,
            )
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    "network-wireless-signal-excellent-symbolic"
                    if network.signal >= 70
                    else "network-wireless-signal-good-symbolic"
                    if network.signal >= 40
                    else "network-wireless-signal-weak-symbolic"
                )
            )
            if network.active:
                row.add_suffix(
                    Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                )
            row.connect("activated", lambda _row: _open_wifi_settings())
            wifi_group.add(row)
            network_rows.append(row)
        refresh_button.set_sensitive(True)

    radio_handler = None

    def _show_radio_state(enabled: bool):
        if radio_handler is not None:
            radio_switch.handler_block(radio_handler)
        radio_switch.set_active(enabled)
        radio_switch.set_state(enabled)
        if radio_handler is not None:
            radio_switch.handler_unblock(radio_handler)
        radio_switch.set_sensitive(True)

    def _apply_scan(enabled: bool, found: tuple[WifiNetwork, ...]):
        _show_radio_state(enabled)
        _replace_network_rows(found)

    def _show_scan_error(error: Exception):
        _replace_network_rows(())
        row = Adw.ActionRow(
            title=_("Unavailable: {error}", lang).format(error=error)
        )
        wifi_group.add(row)
        network_rows.append(row)
        refresh_button.set_sensitive(True)
        if not _is_online():
            status_label.set_label(
                _("Requires an Internet connection. The base installation "
                  "remains available when offline.", lang)
            )
        return False

    def _scan_wifi():
        refresh_button.set_sensitive(False)

        def worker():
            try:
                enabled = wifi_radio_enabled()
                found = scan_wifi_networks() if enabled else ()
            except Exception as error:
                GLib.idle_add(_show_scan_error, error)
            else:
                GLib.idle_add(_apply_scan, enabled, found)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_radio_change(enabled: bool, error: Exception | None):
        if error is not None:
            _show_scan_error(error)
            _scan_wifi()
            return False
        _show_radio_state(enabled)
        _scan_wifi()
        return False

    def _on_radio_state_set(_switch, enabled: bool):
        radio_switch.set_sensitive(False)
        refresh_button.set_sensitive(False)

        def worker():
            error = None
            try:
                set_wifi_radio(enabled)
            except Exception as exception:
                error = exception
            GLib.idle_add(_finish_radio_change, enabled, error)

        threading.Thread(target=worker, daemon=True).start()
        return True

    radio_handler = radio_switch.connect("state-set", _on_radio_state_set)
    refresh_button.connect("clicked", lambda _button: _scan_wifi())
    monitor.connect(
        "network-changed",
        lambda _monitor, _available: (
            _render_connectivity(),
            _scan_wifi(),
        ),
    )

    def on_next():
        shared["network_preflight_skipped"] = not _is_online()
        nav_view.push(build_keyboard_page(shared, nav_view))

    content.append(
        _nav_box(
            lang,
            on_back=lambda: nav_view.pop(),
            on_next=on_next,
            next_label="Continue Installation",
            stage=0,
        )
    )
    page.set_child(content)
    _render_connectivity()
    _scan_wifi()
    return page


# ── page 3: Keyboard layout ──────────────────────────────────────────────

# Physical layouts and their labels come from the same validated policy as
# languages and input methods. New layouts never require a Python edit.
XKB_VARIANTS = list(KEYBOARD_LAYOUTS.items())


def build_keyboard_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    keyboard = shared.get("keyboard", "us")
    page = Adw.NavigationPage(title=_("Keyboard Layout", lang))
    page.set_tag("keyboard")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Keyboard Layout",
            "Confirm your keyboard layout",
            "keyboard",
            lang,
        )
    )

    # Find the index of the current keyboard variant
    variant_names = [v[0] for v in XKB_VARIANTS]
    default_idx = 0
    for i, (code, _name) in enumerate(XKB_VARIANTS):
        if code == keyboard:
            default_idx = i
            break

    # Dropdown
    kbd_store = Gtk.StringList()
    for _code, name in XKB_VARIANTS:
        kbd_store.append(_(name, lang))

    kbd_dropdown = Gtk.DropDown(model=kbd_store)
    kbd_dropdown.set_selected(default_idx)

    def _on_kbd_changed(dd, _pspec):
        idx = dd.get_selected()
        if 0 <= idx < len(XKB_VARIANTS):
            shared["keyboard"] = XKB_VARIANTS[idx][0]

    kbd_dropdown.connect("notify::selected", _on_kbd_changed)

    # Test entry
    test_entry = Gtk.Entry(
        placeholder_text=_("Test your keyboard here…", lang)
    )

    form = card(spacing=16)
    form.set_margin_start(48)
    form.set_margin_end(48)
    form.set_margin_top(48)
    form.set_margin_bottom(12)
    form.append(_labeled(_("Keyboard Layout", lang), kbd_dropdown))
    form.append(_labeled(_("Test your keyboard here…", lang), test_entry))

    recommended_method = _recommended_input_method(shared)
    if recommended_method is not None:
        form.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        input_method_choice = Gtk.CheckButton(
            label=_input_method_install_label(recommended_method, lang)
        )
        input_method_detail = Gtk.Label(
            label=_(
                "Requires an Internet connection. The base installation "
                "remains available when offline.",
                lang,
            ),
            halign=Gtk.Align.START,
            xalign=0,
            wrap=True,
            margin_start=28,
        )
        input_method_detail.add_css_class("dim-label")
        offline_callout = _offline_callout(lang)
        form.append(input_method_choice)
        form.append(input_method_detail)
        form.append(offline_callout)

        preference_key = "_preferred_install_input_method"
        preferred = bool(
            shared.get(
                preference_key,
                shared.get("install_input_method", True),
            )
        )
        shared[preference_key] = preferred
        rendering = {"active": False}
        monitor = Gio.NetworkMonitor.get_default()

        def _save_input_method():
            if rendering["active"]:
                return
            selected = input_method_choice.get_active()
            shared[preference_key] = selected
            shared["install_input_method"] = selected

        def _render_input_method():
            online = internet_connection_ready(monitor)
            shared["network_preflight_online"] = online
            rendering["active"] = True
            input_method_choice.set_active(
                effective_network_choice(
                    bool(shared.get(preference_key, True)), online
                )
            )
            input_method_choice.set_sensitive(online)
            rendering["active"] = False
            offline_callout.set_visible(not online)
            shared["install_input_method"] = (
                input_method_choice.get_active()
            )

        input_method_choice.connect(
            "toggled", lambda _button: _save_input_method()
        )
        monitor.connect(
            "network-changed",
            lambda _monitor, _available: _render_input_method(),
        )
        _render_input_method()
    else:
        shared["install_input_method"] = False
        shared["_preferred_install_input_method"] = False

    form_area = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        vexpand=True,
    )
    form_area.append(clamp_content(form, 720))
    content.append(form_area)

    def on_next():
        nav_view.push(build_software_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    content.append(
        _nav_box(lang, on_back=on_back, on_next=on_next, stage=0)
    )
    page.set_child(content)
    return page


# ── page 3: Updates and drivers ─────────────────────────────────────────

def build_software_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Updates and Drivers", lang))
    page.set_tag("software")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Updates and Drivers",
            "Choose optional software to install",
            "updates",
            lang,
        )
    )

    options = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_start=48,
        margin_end=48,
        margin_top=32,
        vexpand=True,
    )
    options.add_css_class("installer-card")

    offline_callout = _offline_callout(lang)
    options.append(offline_callout)

    updates = Gtk.CheckButton(label=_("Download and install system updates during installation", lang))
    updates_detail = Gtk.Label(
        label=_("Requires an Internet connection. The base installation remains available when offline.", lang),
        halign=Gtk.Align.START,
        wrap=True,
        margin_start=28,
    )
    updates_detail.add_css_class("dim-label")
    options.append(updates)
    options.append(updates_detail)

    drivers = Gtk.CheckButton(label=_("Install third-party drivers for this device", lang))
    drivers_detail = Gtk.Label(
        label=(
            _(
                "Requires an Internet connection. The base installation "
                "remains available when offline.",
                lang,
            )
            + "\n"
            + _(
                "Some drivers are proprietary or otherwise non-free software.",
                lang,
            )
        ),
        halign=Gtk.Align.START,
        wrap=True,
        margin_start=28,
    )
    drivers_detail.add_css_class("dim-label")
    options.append(drivers)
    options.append(drivers_detail)
    content.append(options)

    update_preference_key = "_preferred_install_updates"
    driver_preference_key = "_preferred_install_third_party_drivers"
    shared.setdefault(
        update_preference_key, bool(shared.get("install_updates", True))
    )
    shared.setdefault(
        driver_preference_key,
        bool(shared.get("install_third_party_drivers", False)),
    )
    rendering = {"active": False}
    monitor = Gio.NetworkMonitor.get_default()

    def _save():
        if rendering["active"]:
            return
        if updates.get_sensitive():
            shared[update_preference_key] = updates.get_active()
        if drivers.get_sensitive():
            shared[driver_preference_key] = drivers.get_active()
        shared["install_updates"] = updates.get_active()
        shared["install_third_party_drivers"] = drivers.get_active()

    def _render_connectivity():
        online = internet_connection_ready(monitor)
        shared["network_preflight_online"] = online
        rendering["active"] = True
        updates.set_active(
            effective_network_choice(
                bool(shared.get(update_preference_key, True)), online
            )
        )
        drivers.set_active(
            effective_network_choice(
                bool(shared.get(driver_preference_key, False)), online
            )
        )
        updates.set_sensitive(online)
        drivers.set_sensitive(online)
        rendering["active"] = False
        offline_callout.set_visible(not online)
        shared["install_updates"] = updates.get_active()
        shared["install_third_party_drivers"] = drivers.get_active()

    updates.connect("toggled", lambda _button: _save())
    drivers.connect("toggled", lambda _button: _save())
    monitor.connect(
        "network-changed",
        lambda _monitor, _available: _render_connectivity(),
    )
    _render_connectivity()

    def on_next():
        _save()
        nav_view.push(build_disk_page(shared, nav_view))

    def on_back():
        _save()
        nav_view.pop()

    content.append(
        _nav_box(lang, on_back=on_back, on_next=on_next, stage=0)
    )
    page.set_child(content)
    return page


# ── page 4: Target disk only ─────────────────────────────────────────────

def _disk_card_button(
    choice: StorageDiskChoice, lang: str
) -> Gtk.ToggleButton:
    """Render one physical disk and its current on-disk layout."""

    disk = choice.disk
    identity = disk.identity
    available = not choice.is_live_media
    button = Gtk.ToggleButton(sensitive=available)
    button.add_css_class("disk-card-button")

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    body.add_css_class("disk-card")
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    header.append(icon_picture("one-single-disk", 58))

    identity_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=3,
        hexpand=True,
        valign=Gtk.Align.CENTER,
    )
    model = Gtk.Label(
        label=identity.model or _("Storage Device", lang),
        halign=Gtk.Align.START,
        xalign=0,
        ellipsize=Pango.EllipsizeMode.END,
    )
    model.add_css_class("disk-card-title")
    table = (
        disk.partition_table.upper()
        if disk.partition_table
        else _("No partition table", lang)
    )
    device = Gtk.Label(
        label=f"{identity.path}  ·  {table}",
        halign=Gtk.Align.START,
        xalign=0,
    )
    device.add_css_class("dim-label")
    identity_box.append(model)
    identity_box.append(device)
    header.append(identity_box)

    capacity = Gtk.Label(label=_human_size(identity.expected_size_bytes))
    capacity.add_css_class("storage-badge")
    capacity.set_valign(Gtk.Align.CENTER)
    header.append(capacity)
    selected = Gtk.Image.new_from_icon_name("object-select-symbolic")
    selected.add_css_class("disk-card-check")
    selected.set_valign(Gtk.Align.CENTER)
    header.append(selected)
    body.append(header)

    layout_title = Gtk.Label(
        label=_("Current disk layout", lang),
        halign=Gtk.Align.START,
        xalign=0,
    )
    layout_title.add_css_class("heading")
    body.append(layout_title)

    layout = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        row_spacing=6,
        column_spacing=6,
        max_children_per_line=5,
        min_children_per_line=1,
        homogeneous=False,
    )
    layout.set_halign(Gtk.Align.FILL)

    layout_items = []
    for partition in disk.partitions:
        filesystem = partition.filesystem_type.upper() or _(
            "Unknown filesystem", lang
        )
        label = partition.filesystem_label.strip()
        parts = [
            partition.identity.path,
            filesystem,
        ]
        if label:
            parts.append(label)
        parts.append(_human_size(partition.identity.size_bytes))
        layout_items.append("  ·  ".join(parts))
    if not disk.geometry_probe_error:
        layout_items.extend(
            _("Unallocated · {size}", lang).format(
                size=_human_size(extent.size_bytes)
            )
            for extent in disk.free_extents
            if extent.size_bytes >= _LAYOUT_FREE_SPACE_MINIMUM_BYTES
        )
    if not layout_items:
        empty_layout = (
            N_("Partition details unavailable")
            if disk.geometry_probe_error
            else N_("Empty disk — no partitions")
        )
        layout_items.append(
            _(empty_layout, lang)
        )
    for description in layout_items:
        chip = Gtk.Label(label=description)
        chip.add_css_class("partition-chip")
        layout.insert(chip, -1)
    body.append(layout)

    notices = []
    if choice.coexistence.windows_detected:
        notices.append(_("Windows detected", lang))
    if choice.coexistence.bitlocker_detected:
        notices.append(_("BitLocker detected", lang))
    if choice.is_live_media:
        notices.append(_("Live USB — excluded", lang))
    elif not choice.erase_available:
        notices.append(_("Too small", lang))
    if notices:
        notice = Gtk.Label(
            label="  ·  ".join(notices),
            halign=Gtk.Align.START,
            xalign=0,
            wrap=True,
        )
        notice.add_css_class("disk-card-notice")
        body.append(notice)

    button.set_child(body)
    return button


def build_disk_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Select Installation Disk", lang))
    page.set_tag("disk")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Select Installation Disk",
            "Choose one target disk. Storage settings come next.",
            "select-installation-disk",
            lang,
        )
    )

    disk_choices: list[StorageDiskChoice | None] = []
    disk_buttons: list[Gtk.ToggleButton] = []
    disk_list = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        vexpand=True,
    )
    disk_list.add_css_class("disk-card-list")
    disk_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        margin_start=48,
        margin_end=48,
        vexpand=True,
    )
    disk_scroll.set_child(disk_list)
    disk_scroll.add_css_class("disk-card-scroll")
    content.append(disk_scroll)

    status = Gtk.Label(
        halign=Gtk.Align.CENTER,
        wrap=True,
        margin_start=48,
        margin_end=48,
    )
    status.add_css_class("dim-label")
    status.add_css_class("installer-warning-card")
    status.set_visible(False)
    content.append(status)

    rescan = Gtk.Button(label=_("Rescan Storage", lang))
    rescan.set_halign(Gtk.Align.CENTER)
    content.append(rescan)

    next_button = None

    def _set_next(enabled: bool):
        if next_button is not None:
            next_button.set_sensitive(enabled)

    def _selected_choice() -> StorageDiskChoice | None:
        return next(
            (
                choice
                for choice, button in zip(disk_choices, disk_buttons)
                if choice is not None and button.get_active()
            ),
            None,
        )

    def _on_disk_selected():
        choice = _selected_choice()
        if choice is None or choice.is_live_media:
            _set_next(False)
            return
        bind_storage_target(shared, choice)
        status.set_visible(False)
        _set_next(True)

    def _populate_disks(*, restore_selection: bool):
        nonlocal disk_choices, disk_buttons
        previous_id = (
            str(shared.get("disk_stable_id") or "")
            if restore_selection
            else ""
        )
        child = disk_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            disk_list.remove(child)
            child = following
        disk_choices = []
        disk_buttons = []
        status.set_visible(False)
        _set_next(False)
        try:
            workflow = build_storage_workflow(
                probe_storage_inventory(),
                probe_platform(),
                live_device=_find_live_device(),
            )
            first_button = None
            for choice in workflow.disks:
                button = _disk_card_button(choice, lang)
                if first_button is None:
                    first_button = button
                else:
                    button.set_group(first_button)
                button.connect(
                    "toggled", lambda _button: _on_disk_selected()
                )
                disk_list.append(button)
                disk_buttons.append(button)
                disk_choices.append(choice)
        except ProbeError as error:
            status.set_label(str(error))
            status.set_visible(True)

        if not disk_choices:
            empty = Gtk.Label(
                label=_("No suitable disks found.", lang),
                margin_top=28,
                margin_bottom=28,
            )
            empty.add_css_class("dim-label")
            disk_list.append(empty)
            disk_choices.append(None)
            return

        selected_index = next(
            (
                index
                for index, choice in enumerate(disk_choices)
                if previous_id
                and choice is not None
                and choice.disk.identity.stable_id == previous_id
                and choice.disk.identity.expected_size_bytes
                == int(shared.get("disk_size_bytes") or 0)
            ),
            None,
        )
        if selected_index is not None:
            disk_buttons[selected_index].set_active(True)

    def _rescan():
        clear_storage_target(shared)
        _populate_disks(restore_selection=False)

    shared["_rescan_disk_page"] = _rescan
    rescan.connect("clicked", lambda _button: _rescan())

    def on_next():
        if _selected_choice() is None:
            return
        nav_view.push(build_storage_strategy_page(shared, nav_view))

    nav = _nav_box(
        lang,
        on_back=lambda: nav_view.pop(),
        on_next=on_next,
        next_sensitive=False,
        stage=1,
    )
    next_button = nav.next_button
    _populate_disks(restore_selection=True)
    content.append(nav)
    page.set_child(content)
    return page


# ── page 5: Storage strategy ─────────────────────────────────────────────

def build_storage_strategy_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Choose Installation Method", lang))
    page.set_tag("storage-strategy")
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    disk_subtitle = " · ".join(
        str(value)
        for value in (
            shared.get("disk_model", "?"),
            shared.get("disk_size", "?"),
            shared.get("disk", "?"),
        )
        if value
    )
    content.append(
        _page_header(
            "How should AnduinOS use this disk?",
            disk_subtitle,
            "how-should-use",
            lang,
        )
    )

    options = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_start=72,
        margin_end=72,
        margin_top=4,
        vexpand=True,
    )
    options.add_css_class("strategy-options")
    strategy_buttons: dict[StorageStrategy, Gtk.ToggleButton] = {}
    first_button = None

    def _add_strategy(
        strategy,
        title,
        subtitle,
        icon,
        *,
        enabled=True,
    ):
        nonlocal first_button
        button = Gtk.ToggleButton(sensitive=enabled)
        button.add_css_class("strategy-card")
        if first_button is None:
            first_button = button
        else:
            button.set_group(first_button)

        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16,
        )
        row.append(icon_picture(icon, 56))
        copy = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        title_label = Gtk.Label(
            label=title,
            halign=Gtk.Align.START,
            xalign=0,
        )
        title_label.add_css_class("strategy-card-title")
        subtitle_label = Gtk.Label(
            label=subtitle,
            halign=Gtk.Align.START,
            xalign=0,
            wrap=True,
        )
        subtitle_label.add_css_class("dim-label")
        copy.append(title_label)
        copy.append(subtitle_label)
        row.append(copy)
        selected = Gtk.Image.new_from_icon_name("object-select-symbolic")
        selected.add_css_class("strategy-check")
        selected.set_valign(Gtk.Align.CENTER)
        row.append(selected)
        button.set_child(row)
        options.append(button)
        strategy_buttons[strategy] = button

    erase_available = bool(shared.get("disk_erase_available"))
    erase_warning = _(
        "Erase every partition and all data on the selected disk.", lang
    )
    _add_strategy(
        StorageStrategy.ERASE_BTRFS,
        _("Btrfs — recommended", lang),
        erase_warning
        + " "
        + _(
            "Enables shared-space subvolumes, snapshots and Timeback Machine.",
            lang,
        ),
        "btrfs",
        enabled=erase_available,
    )
    _add_strategy(
        StorageStrategy.ERASE_EXT4,
        _("ext4 — classic", lang),
        erase_warning
        + " "
        + _("Uses a traditional single root filesystem.", lang),
        "ext4",
        enabled=erase_available,
    )
    _add_strategy(
        StorageStrategy.ADVANCED_COEXISTENCE,
        _("Advanced — keep existing systems", lang),
        _(
            "Use already-unallocated space and preserve existing partitions. "
            "This is the only Windows coexistence path.",
            lang,
        ),
        "advanced",
    )
    content.append(options)

    warning_box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=12,
        halign=Gtk.Align.CENTER,
        margin_start=72,
        margin_end=72,
    )
    warning_box.add_css_class("strategy-warning")
    warning_icon = icon_picture("flashing-disk", 38)
    warning = Gtk.Label(wrap=True, xalign=0)
    warning.add_css_class("warning")
    warning_box.append(warning_icon)
    warning_box.append(warning)
    content.append(warning_box)
    next_button = None

    def _set_next(enabled):
        if next_button is not None:
            next_button.set_sensitive(enabled)

    def _selected_strategy() -> StorageStrategy | None:
        return next(
            (
                strategy for strategy, button in strategy_buttons.items()
                if button.get_active()
            ),
            None,
        )

    def _set_warning(message, icon):
        warning.set_label(message)
        warning_icon.set_paintable(
            icon_picture(icon, 38).get_paintable()
        )

    def _show_strategy(strategy):
        apply_storage_strategy(shared, strategy)
        if strategy is StorageStrategy.ADVANCED_COEXISTENCE:
            _set_warning(
                _(
                    "Advanced coexistence can affect EFI firmware state and "
                    "may trigger BitLocker recovery. It never shrinks or "
                    "repairs Windows volumes.",
                    lang,
                ),
                "advanced",
            )
        else:
            _set_warning(
                _(
                    "ALL DATA on {disk} will be permanently erased.", lang
                ).format(disk=shared.get("disk", "?")),
                "flashing-disk",
            )
        _set_next(True)

    for strategy, button in strategy_buttons.items():
        button.connect(
            "toggled",
            lambda toggled, selected=strategy: (
                _show_strategy(selected) if toggled.get_active() else None
            ),
        )

    existing = str(shared.get("storage_strategy") or "")
    restored = next(
        (item for item in StorageStrategy if item.value == existing),
        None,
    )
    if restored in {
        StorageStrategy.ERASE_BTRFS,
        StorageStrategy.ERASE_EXT4,
    } and not erase_available:
        restored = None
        clear_guided_storage_selection(shared)
        shared["storage_strategy"] = ""
    if restored is not None:
        strategy_buttons[restored].set_active(True)
    elif (
        erase_available
        and not bool(shared.get("disk_has_existing_partitions"))
    ):
        strategy_buttons[StorageStrategy.ERASE_BTRFS].set_active(True)
    elif not erase_available:
        _set_warning(
            _(
                "This disk is too small for whole-disk installation. "
                "Advanced may continue only if suitable unallocated space "
                "exists.",
                lang,
            ),
            "advanced",
        )
    else:
        _set_warning(
            _(
                "Existing partitions were detected. The first two choices "
                "delete them; Advanced is the preservation path.",
                lang,
            ),
            "advanced",
        )

    def on_next():
        strategy = _selected_strategy()
        if strategy is None:
            return
        if strategy is StorageStrategy.ADVANCED_COEXISTENCE:
            nav_view.push(build_advanced_storage_page(shared, nav_view))
        else:
            nav_view.push(build_user_page(shared, nav_view))

    nav = _nav_box(
        lang,
        on_back=lambda: nav_view.pop(),
        on_next=on_next,
        next_sensitive=_selected_strategy() is not None,
        stage=1,
    )
    next_button = nav.next_button
    content.append(nav)
    page.set_child(content)
    return page


# ── page 6: Advanced coexistence storage ────────────────────────────────

def build_advanced_storage_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Advanced Storage", lang))
    page.set_tag("advanced-storage")
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    content.append(
        _page_header(
            "Advanced: Install Alongside",
            "Use only existing unallocated space on the selected disk.",
            "advanced",
            lang,
        )
    )

    target = Gtk.Label(
        label=_("Target: {disk} ({size} — {model})", lang).format(
            disk=shared.get("disk", "?"),
            size=shared.get("disk_size", "?"),
            model=shared.get("disk_model", "?"),
        ),
        halign=Gtk.Align.CENTER,
        wrap=True,
    )
    target.add_css_class("dim-label")
    target.add_css_class("installer-callout")
    content.append(target)

    risk = Gtk.Label(
        wrap=True,
        halign=Gtk.Align.FILL,
        xalign=0,
        hexpand=True,
    )
    risk.add_css_class("warning")
    if shared.get("disk_windows_detected"):
        risk_text = _(
            "Before continuing: finish Windows updates, disable Fast Startup "
            "and hibernation, and back up the BitLocker recovery key. EFI, "
            "Secure Boot, TPM measurements and boot order can change.",
            lang,
        )
    else:
        risk_text = _(
            "This advanced path preserves existing partitions but changes "
            "the selected disk's partition table and the machine's EFI boot "
            "state.",
            lang,
        )
    risk.set_label(risk_text)
    risk_card = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=14,
        margin_start=48,
        margin_end=48,
    )
    risk_card.add_css_class("installer-warning-card")
    risk_card.append(icon_picture("secure-boot", 42))
    risk_card.append(risk)
    content.append(risk_card)

    controls = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_start=64,
        margin_end=64,
        margin_top=8,
        vexpand=True,
    )
    controls.add_css_class("installer-card")
    filesystem_row = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=12,
        halign=Gtk.Align.START,
    )
    filesystem_row.append(Gtk.Label(label=_("Filesystem", lang)))
    filesystem = Gtk.DropDown(
        model=Gtk.StringList.new(
            [_('Btrfs (recommended)', lang), _("ext4 (classic)", lang)]
        )
    )
    filesystem.set_selected(
        1 if shared.get("filesystem") == Filesystem.EXT4.value else 0
    )
    filesystem_row.append(filesystem)
    controls.append(filesystem_row)
    controls.append(
        Gtk.Label(
            label=_("Unallocated space", lang),
            halign=Gtk.Align.START,
        )
    )
    extent_dropdown = Gtk.DropDown()
    controls.append(extent_dropdown)
    controls.append(
        Gtk.Label(
            label=_("EFI System Partition", lang),
            halign=Gtk.Align.START,
        )
    )
    esp_dropdown = Gtk.DropDown()
    controls.append(esp_dropdown)
    guidance = Gtk.Label(
        halign=Gtk.Align.START,
        wrap=True,
        selectable=True,
    )
    guidance.add_css_class("dim-label")
    guidance.add_css_class("installer-callout")
    controls.append(guidance)
    rescan = Gtk.Button(label=_("Rescan and Reselect Disk", lang))
    rescan.set_halign(Gtk.Align.START)
    controls.append(rescan)
    controls_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vexpand=True,
    )
    controls_scroll.set_child(clamp_content(controls, 820))
    content.append(controls_scroll)

    workflow: StorageWorkflow | None = None
    selected_choice: StorageDiskChoice | None = None
    free_candidates = []
    esp_options = []
    updating_controls = False
    next_button = None

    def _set_next(enabled):
        if next_button is not None:
            next_button.set_sensitive(enabled)

    def _selected_extent():
        position = extent_dropdown.get_selected()
        if 0 <= position < len(free_candidates):
            return free_candidates[position]
        return None

    def _configure_esp_options():
        nonlocal esp_options, updating_controls
        candidate = _selected_extent()
        if selected_choice is None or candidate is None:
            esp_options = []
            esp_dropdown.set_model(Gtk.StringList.new([]))
            return
        options = list(selected_choice.coexistence.esp_candidates)
        if not candidate.requires_reused_esp:
            options.append(None)
        esp_options = options
        labels = [
            (
                _(
                    "Create a new 1 GiB AnduinOS EFI System Partition",
                    lang,
                )
                if option is None
                else _("Reuse {path} ({size})", lang).format(
                    path=option.identity.path,
                    size=_human_size(option.identity.size_bytes),
                )
            )
            for option in options
        ]
        updating_controls = True
        esp_dropdown.set_model(Gtk.StringList.new(labels))
        preferred = str(shared.get("guided_esp_partuuid") or "")
        selected_index = next(
            (
                index
                for index, option in enumerate(options)
                if (
                    option.identity.partuuid if option is not None else ""
                )
                == preferred
            ),
            0,
        )
        esp_dropdown.set_selected(selected_index)
        updating_controls = False

    def _guided_selection() -> GuidedStorageSelection | None:
        candidate = _selected_extent()
        esp_position = esp_dropdown.get_selected()
        if (
            selected_choice is None
            or candidate is None
            or not (0 <= esp_position < len(esp_options))
        ):
            return None
        esp = esp_options[esp_position]
        return GuidedStorageSelection(
            disk_stable_id=selected_choice.disk.identity.stable_id,
            disk_size_bytes=(
                selected_choice.disk.identity.expected_size_bytes
            ),
            free_extent_id=candidate.extent.extent_id,
            reused_esp_partuuid=(
                esp.identity.partuuid if esp is not None else ""
            ),
            filesystem=Filesystem(str(shared.get("filesystem", "btrfs"))),
        )

    def _load_workflow():
        nonlocal workflow, selected_choice, free_candidates, updating_controls
        clear_guided_storage_selection(shared)
        _set_next(False)
        try:
            workflow = build_storage_workflow(
                probe_storage_inventory(),
                probe_platform(),
                live_device=_find_live_device(),
            )
            candidate = workflow.disk(
                str(shared.get("disk_stable_id") or "")
            )
        except (ProbeError, KeyError) as error:
            workflow = None
            selected_choice = None
            guidance.set_label(
                _(
                    "The selected disk changed or disappeared. Return to the "
                    "disk page and select it again. {error}",
                    lang,
                ).format(error=error)
            )
            return
        if (
            candidate.disk.identity.expected_size_bytes
            != int(shared.get("disk_size_bytes") or 0)
        ):
            selected_choice = None
            guidance.set_label(
                _(
                    "The selected disk size changed. Return and select it "
                    "again.",
                    lang,
                )
            )
            return
        if bind_storage_target(shared, candidate):
            selected_choice = None
            guidance.set_label(
                _(
                    "The selected disk topology changed. Review the disk and "
                    "installation method again.",
                    lang,
                )
            )
            return
        selected_choice = candidate
        target.set_label(
            _("Target: {disk} ({size} — {model})", lang).format(
                disk=candidate.disk.identity.path,
                size=_human_size(
                    candidate.disk.identity.expected_size_bytes
                ),
                model=candidate.disk.identity.model,
            )
        )
        decision = candidate.coexistence
        guidance.set_label(
            "\n\n".join(
                _coexistence_notice_text(
                    item,
                    lang,
                    decision.windows_detected,
                )
                for item in decision.notices
                if item.code
                is not CoexistenceNoticeCode.DISPOSABLE_PARTITION_OPTION
            )
        )
        if not candidate.guided_available:
            free_candidates = []
            extent_dropdown.set_model(Gtk.StringList.new([]))
            esp_dropdown.set_model(Gtk.StringList.new([]))
            return
        free_candidates = list(decision.free_space_candidates)
        names = [
            _("{size} unallocated at {offset}", lang).format(
                size=_human_size(item.extent.size_bytes),
                offset=_human_size(item.extent.start_bytes),
            )
            for item in free_candidates
        ]
        updating_controls = True
        extent_dropdown.set_model(Gtk.StringList.new(names))
        extent_dropdown.set_selected(0)
        updating_controls = False
        _configure_esp_options()
        _set_next(_guided_selection() is not None)

    def _filesystem_changed():
        shared["filesystem"] = (
            Filesystem.EXT4.value
            if filesystem.get_selected() == 1
            else Filesystem.BTRFS.value
        )
        shared["guided_storage_preview_model"] = None

    filesystem.connect(
        "notify::selected",
        lambda _widget, _pspec: _filesystem_changed(),
    )

    def _extent_changed():
        if updating_controls:
            return
        _configure_esp_options()
        _set_next(_guided_selection() is not None)

    extent_dropdown.connect(
        "notify::selected", lambda _widget, _pspec: _extent_changed()
    )
    esp_dropdown.connect(
        "notify::selected",
        lambda _widget, _pspec: (
            None
            if updating_controls
            else _set_next(_guided_selection() is not None)
        ),
    )

    def _rescan_and_reselect():
        clear_storage_target(shared)
        disk_rescan = shared.get("_rescan_disk_page")
        if callable(disk_rescan):
            disk_rescan()
        nav_view.pop_to_tag("disk")

    rescan.connect("clicked", lambda _button: _rescan_and_reselect())

    def on_next():
        if workflow is None:
            return
        selected = _guided_selection()
        if selected is None:
            return
        try:
            preview = build_guided_storage_preview(workflow, selected)
        except ValueError as error:
            guidance.set_label(str(error))
            _set_next(False)
            return
        shared["guided_extent_id"] = selected.free_extent_id
        shared["guided_esp_partuuid"] = selected.reused_esp_partuuid
        shared["guided_storage_preview_model"] = preview
        nav_view.push(build_user_page(shared, nav_view))

    nav = _nav_box(
        lang,
        on_back=lambda: nav_view.pop(),
        on_next=on_next,
        next_sensitive=False,
        stage=1,
    )
    next_button = nav.next_button
    _filesystem_changed()
    _load_workflow()
    content.append(nav)
    page.set_child(content)
    return page


def _find_live_device():
    """Heuristic: find the block device backing /cdrom or /rofs."""
    try:
        import subprocess
        # Check common live media mount points
        for mp in ["/cdrom", "/run/live/medium"]:
            out = subprocess.check_output(
                ["findmnt", "-n", "-o", "SOURCE", mp],
                text=True, timeout=3,
            ).strip()
            if out and out.startswith("/dev/"):
                # Strip partition number to get the base device
                return _base_device(out)
    except Exception:
        pass
    return ""


def _human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def _base_device(dev_path: str) -> str:
    """Strip partition suffix from a device path.  /dev/sda1 → /dev/sda"""
    import re
    m = re.match(r"(/dev/(?:nvme\d+n\d+|mmcblk\d+|sd[a-z]+|vd[a-z]+))\d+", dev_path)
    if m:
        return m.group(1)
    # If it's already a base device, return it
    return dev_path


# ── page 7: User account ─────────────────────────────────────────────────

def build_user_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("User Account", lang))
    page.set_tag("user")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "User Account",
            "Create your user account",
            "account",
            lang,
        )
    )

    # Validation state
    valid = {"name": True, "pass": True, "host": True}
    guided_account = (
        shared.get("storage_mode")
        == InstallMode.GUIDED_COEXISTENCE.value
    )

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                  spacing=8, margin_start=48, margin_end=48,
                  margin_top=12, vexpand=True)
    box.add_css_class("installer-card")

    # Full name
    full_entry = Gtk.Entry(
        placeholder_text=_("Full Name", lang), max_length=128
    )
    box.append(_labeled(_("Full Name", lang), full_entry))

    # Username
    user_entry = Gtk.Entry(
        placeholder_text=_("Username", lang), max_length=16
    )
    name_warn = Gtk.Label(visible=False)
    name_warn.add_css_class("warning")
    box.append(_labeled(_("Username", lang), user_entry))
    box.append(name_warn)

    # Password
    pass_entry = Gtk.Entry(placeholder_text=_("Password", lang),
                           visibility=False)
    pass_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    confirm_entry = Gtk.Entry(
        placeholder_text=_("Confirm Password", lang),
        visibility=False,
    )
    confirm_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    pass_warn = Gtk.Label(visible=False)
    pass_warn.add_css_class("warning")

    box.append(_labeled(_("Password", lang), pass_entry))
    box.append(_labeled(_("Confirm Password", lang), confirm_entry))
    box.append(pass_warn)

    sudo_without_password = Gtk.CheckButton(
        label=_("Do not require a password for sudo commands", lang)
    )
    box.append(sudo_without_password)

    # Hostname
    host_entry = Gtk.Entry(
        placeholder_text=_("Computer Name", lang),
        text=shared.get("hostname", "anduinos"),
    )
    host_warn = Gtk.Label(visible=False)
    host_warn.add_css_class("warning")
    box.append(_labeled(_("Computer Name", lang), host_entry))
    box.append(host_warn)

    # Auto-transliterate full name → username until the user edits it.
    username_state = {"user_edited": False, "setting_suggestion": False}

    def _on_username_changed(_entry):
        if not username_state["setting_suggestion"]:
            username_state["user_edited"] = True

    def _on_full_changed(entry):
        full = entry.get_text()
        shared["full_name"] = full
        if not username_state["user_edited"]:
            username_state["setting_suggestion"] = True
            try:
                user_entry.set_text(suggest_username(full))
            finally:
                username_state["setting_suggestion"] = False

    user_entry.connect("changed", _on_username_changed)
    full_entry.connect("changed", _on_full_changed)

    # Validate on change
    HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")

    def _validate():
        uname = user_entry.get_text()
        pword = pass_entry.get_text()
        confirmation = confirm_entry.get_text()
        host = host_entry.get_text()

        if uname and not is_valid_username(uname):
            name_warn.set_label(_("Username must start with a lowercase ASCII letter and contain only lowercase ASCII letters or digits (maximum 16 characters).", lang))
            name_warn.set_visible(True)
            valid["name"] = False
        else:
            name_warn.set_visible(False)
            valid["name"] = bool(uname)

        if not pword and not confirmation:
            if guided_account:
                pass_warn.set_label(
                    _(
                        "Install alongside requires a password-protected "
                        "account.",
                        lang,
                    )
                )
                pass_warn.set_visible(True)
                valid["pass"] = False
            else:
                pass_warn.set_visible(False)
                valid["pass"] = True
        elif pword != confirmation:
            pass_warn.set_label(_("The two passwords do not match.", lang))
            pass_warn.set_visible(True)
            valid["pass"] = False
        elif pword and len(pword) < 6:
            pass_warn.set_label(_("Password must be at least 6 characters.", lang))
            pass_warn.set_visible(True)
            valid["pass"] = False
        else:
            pass_warn.set_visible(False)
            valid["pass"] = len(pword) >= 6

        if host and not HOST_RE.match(host):
            host_warn.set_label(_("Computer name contains invalid characters.", lang))
            host_warn.set_visible(True)
            valid["host"] = False
        else:
            host_warn.set_visible(False)
            valid["host"] = bool(host)

        all_valid = valid["name"] and valid["pass"] and valid["host"]
        nxt_btn.set_sensitive(all_valid)

    user_entry.connect("changed", lambda _e: _validate())
    pass_entry.connect("changed", lambda _e: _validate())
    confirm_entry.connect("changed", lambda _e: _validate())

    def _set_sudo_without_password(button):
        enabled = button.get_active()
        shared["sudo_without_password"] = enabled
        _validate()

    sudo_without_password.connect("toggled", _set_sudo_without_password)

    def _clear_password_ui():
        pass_entry.set_text("")
        confirm_entry.set_text("")

    shared["_clear_password_ui"] = _clear_password_ui
    host_entry.connect("changed", lambda _e: _validate())

    account_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vexpand=True,
    )
    account_scroll.set_child(clamp_content(box, 760))
    content.append(account_scroll)

    def _save_account_state():
        shared["username"] = user_entry.get_text()
        shared["password"] = pass_entry.get_text()
        shared["password_confirmation"] = confirm_entry.get_text()
        shared["passwordless_shared"] = not pass_entry.get_text()
        shared["sudo_without_password"] = sudo_without_password.get_active()
        shared["hostname"] = host_entry.get_text()

    def _continue_to_timezone():
        _save_account_state()
        nav_view.push(build_timezone_page(shared, nav_view))

    def _show_message(heading_key, body_key):
        dialog = Adw.MessageDialog(
            transient_for=nav_view.get_root(),
            heading=_(heading_key, lang),
            body=_(body_key, lang),
        )
        dialog.add_response("back", _("Return to Make Changes", lang))
        dialog.set_default_response("back")
        dialog.set_close_response("back")
        dialog.present()

    def _confirm_unsafe_sudo(passwordless):
        warning_heading = (
            N_("This configuration is very unsafe")
            if passwordless
            else N_("Passwordless sudo is unsafe")
        )
        warning_body = (
            N_(
                "The system will sign in to this account automatically and "
                "allow it to obtain full administrator privileges without a "
                "password. Anyone with physical access, and any program "
                "running as this user, can completely control the system. "
                "Use this only for a kiosk, temporary virtual machine, or "
                "another controlled environment."
            )
            if passwordless
            else N_(
                "Any program running as your user can obtain full "
                "administrator privileges without authentication. Your login "
                "password still protects sign-in, but it will not protect "
                "sudo. Are you sure you want to continue?"
            )
        )
        dialog = Adw.MessageDialog(
            transient_for=nav_view.get_root(),
            heading=_(warning_heading, lang),
            body=_(warning_body, lang),
        )
        dialog.add_response(
            "back",
            _(
                "Return and Set a Password"
                if passwordless
                else "Return to Make Changes",
                lang,
            ),
        )
        dialog.add_response("continue", _("I Understand the Risk, Continue", lang))
        dialog.set_response_appearance(
            "continue", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("back")
        dialog.set_close_response("back")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                _continue_to_timezone()
                if response == "continue"
                else None
            ),
        )
        dialog.present()

    def on_next():
        action = account_next_action(
            pass_entry.get_text(),
            confirm_entry.get_text(),
            sudo_without_password.get_active(),
        )
        if action is AccountNextAction.BLOCK_LOCKOUT:
            _show_message(
                N_("Administrator access would be locked"),
                N_(
                    "This account has no login password, but sudo would still "
                    "require one. Because the root account is locked by "
                    "default, you would be unable to perform administrator "
                    "tasks. Set an account password or enable passwordless "
                    "sudo."
                ),
            )
            return
        if action in {
            AccountNextAction.CONFIRM_PASSWORDLESS_SUDO,
            AccountNextAction.CONFIRM_SUDO,
        }:
            _confirm_unsafe_sudo(
                action is AccountNextAction.CONFIRM_PASSWORDLESS_SUDO
            )
            return
        _continue_to_timezone()

    def on_back():
        nav_view.pop()

    nav = _nav_box(
        lang,
        on_back=on_back,
        on_next=on_next,
        next_sensitive=False,
        stage=2,
    )
    nxt_btn = nav.next_button
    content.append(nav)
    page.set_child(content)
    return page


def _labeled(label_text, widget):
    """Wrap widget in a simple label-above-widget layout."""
    g = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    lbl = Gtk.Label(label=label_text, halign=Gtk.Align.START)
    lbl.add_css_class("heading")
    g.append(lbl)
    g.append(widget)
    return g


# ── page 8: Timezone ─────────────────────────────────────────────────────

def build_timezone_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Select Timezone", lang))
    page.set_tag("timezone")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Select Timezone",
            "Choose your location to set the system clock",
            "timezone",
            lang,
        )
    )

    # Load timezone list
    zones = _load_timezones()

    list_store = Gtk.StringList.new(zones)

    # Search entry
    search = Gtk.SearchEntry(placeholder_text=_("Search timezones…", lang),
                             margin_start=48, margin_end=48)

    # Filter model
    filter_model = Gtk.FilterListModel(model=list_store)
    def _filter(item):
        query = search.get_text().lower()
        if not query:
            return True
        tz = item.get_string()
        return query in tz.lower()

    timezone_filter = Gtk.CustomFilter.new(_filter)
    filter_model.set_filter(timezone_filter)

    factory = Gtk.SignalListItemFactory()
    def _tz_setup(_f, item):
        item.set_child(_list_item_row())
    def _tz_bind(_f, item):
        row = item.get_child()
        _bind_list_item_row(row, item.get_item().get_string())
    factory.connect("setup", _tz_setup)
    factory.connect("bind", _tz_bind)

    tz_list = Gtk.ListView(model=Gtk.SingleSelection(model=filter_model),
                           factory=factory, vexpand=True)
    tz_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                   margin_start=48, margin_end=48,
                                   vexpand=True)
    tz_scroll.set_child(tz_list)
    tz_scroll.add_css_class("installer-list-card")

    search.connect("search-changed", lambda _s: timezone_filter.changed(
        Gtk.FilterChange.DIFFERENT))

    sel = tz_list.get_model()
    # Filtering must not silently replace the user's timezone with the first
    # search result.
    sel.set_autoselect(False)
    selected_label = Gtk.Label(
        halign=Gtk.Align.START,
        margin_start=48,
        margin_end=48,
    )
    selected_label.add_css_class("heading")
    selected_label.add_css_class("installer-callout")

    def _on_tz_selected():
        pos = sel.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            timezone = filter_model.get_item(pos).get_string()
            shared["timezone"] = timezone
            selected_label.set_label(
                f"{_('Selected timezone', lang)}: {timezone}"
            )

    sel.connect("selection-changed", lambda _s, _p, _n: _on_tz_selected())

    # Select and reveal the maintained language default. Gtk.ListView is
    # virtualized, so selecting an off-screen row alone does not make it
    # visible.
    current_tz = str(shared.get("timezone") or "America/New_York")
    try:
        selected_position = zones.index(current_tz)
    except ValueError:
        selected_position = zones.index("America/New_York")
    sel.select_item(selected_position, True)
    _on_tz_selected()
    GLib.idle_add(
        lambda: (
            tz_list.scroll_to(
                selected_position,
                Gtk.ListScrollFlags.SELECT | Gtk.ListScrollFlags.FOCUS,
                None,
            ),
            False,
        )[1]
    )

    content.append(search)
    content.append(selected_label)
    content.append(tz_scroll)

    def on_next():
        nav_view.push(build_summary_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    content.append(
        _nav_box(lang, on_back=on_back, on_next=on_next, stage=2)
    )
    page.set_child(content)
    return page


def _load_timezones():
    """Read timezone list from the system."""
    zones = []
    try:
        with open("/usr/share/zoneinfo/zone.tab", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    zones.append(parts[2])  # e.g., "Asia/Shanghai"
    except FileNotFoundError:
        zones = ["UTC", "America/New_York", "America/Chicago",
                 "America/Denver", "America/Los_Angeles",
                 "Europe/London", "Europe/Berlin", "Europe/Paris",
                 "Asia/Shanghai", "Asia/Tokyo", "Asia/Seoul",
                 "Australia/Sydney"]
    return sorted(zones)


# ── page 9: Summary ──────────────────────────────────────────────────────

def build_summary_page(shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Ready to Install", lang))
    page.set_tag("summary")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Ready to Install",
            "Please review your choices before proceeding",
            "review",
            lang,
        )
    )
    development_mode = bool(shared.get("development_mode"))
    guided_mode = (
        shared.get("storage_mode")
        == InstallMode.GUIDED_COEXISTENCE.value
    )
    guided_preview = shared.get("guided_storage_preview_model")
    if development_mode:
        development_banner = Gtk.Label(
            label=_(
                "DEVELOPMENT MODE — the plan will be validated and simulated. "
                "No privileged executor or disk command can run.",
                lang,
            ),
            margin_start=48,
            margin_end=48,
            margin_top=12,
            wrap=True,
        )
        development_banner.add_css_class("warning")
        development_banner.add_css_class("installer-warning-card")
        content.append(development_banner)

    # Build summary text
    lang_name = "English"
    for l in LANGUAGES:
        if l.code == shared.get("lang"):
            lang_name = f"{l.english_name} ({l.native_name})"
            break

    secure_boot_enabled = False
    platform = None
    try:
        platform = probe_platform()
        secure_boot_enabled = platform.secure_boot is SecureBoot.ENABLED
        platform_text = _(
            "{architecture} / {firmware} / Secure Boot: {secure_boot}",
            lang,
        ).format(
            architecture=platform.architecture.value,
            firmware=platform.firmware.value,
            secure_boot=platform.secure_boot.value,
        )
        platform_error = ""
    except ProbeError as error:
        platform_text = _("Unavailable: {error}", lang).format(error=error)
        platform_error = str(error)

    escape = lambda value: html.escape(str(value))
    filesystem = str(shared.get("filesystem", "btrfs"))
    storage_detail = (
        ", ".join(
            f"{item.name}→{item.mount_point}" for item in BTRFS_SUBVOLUMES
        )
        if filesystem == "btrfs"
        else _("single ext4 root filesystem", lang)
    )
    swap_sizing = None
    try:
        swap_sizing = (
            guided_preview.swap_sizing
            if guided_mode and isinstance(guided_preview, GuidedStoragePreview)
            else calculate_swap_sizing(
                probe_physical_memory_bytes(),
                int(shared.get("disk_size_bytes") or 0),
            )
        )
    except (RuntimeError, ValueError):
        pass
    lines = [
        f"<b>{_('Language', lang)}:</b> {lang_name}",
        f"<b>{_('Keyboard', lang)}:</b> "
        f"{escape(shared.get('keyboard', 'us'))}",
        f"<b>{_('Target Disk', lang)}:</b> "
        f"{escape(shared.get('disk', '?'))} "
        f"({escape(shared.get('disk_size', '?'))} — "
        f"{escape(shared.get('disk_model', '?'))})",
        f"<b>{_('Stable disk identity', lang)}:</b> "
        f"{escape(shared.get('disk_stable_id', '?'))}",
        f"<b>{_('Platform', lang)}:</b> {escape(platform_text)}",
        f"<b>{_('Filesystem', lang)}:</b> {escape(filesystem)}",
        f"<b>{_('Subvolumes', lang)}:</b> {escape(storage_detail)}",
        f"<b>{_('System updates', lang)}:</b> "
        + (
            _("download and install", lang)
            if shared.get("install_updates", True)
            else _("do not install", lang)
        ),
        f"<b>{_('Third-party drivers', lang)}:</b> "
        + (
            _("detect and install (may include non-free software)", lang)
            if shared.get("install_third_party_drivers", False)
            else _("do not install", lang)
        ),
        f"<b>{_('Secure Boot enrollment', lang)}:</b> "
        + (
            _(
                "create a machine-local MOK; enroll after reboot with "
                "password 123456",
                lang,
            )
            if secure_boot_enabled
            else _("not required", lang)
        ),
        f"<b>{_('User', lang)}:</b> "
        f"{escape(shared.get('full_name', '?'))} "
        f"({escape(shared.get('username', '?'))})",
        f"<b>{_('Account security', lang)}:</b> "
        + (
            _("automatic login", lang)
            if shared.get("passwordless_shared", False)
            else _("password required for login", lang)
        )
        + (
            _("; sudo does not require a password", lang)
            if shared.get("sudo_without_password", False)
            else _("; sudo requires the account password", lang)
        ),
        f"<b>{_('Computer Name', lang)}:</b> "
        f"{escape(shared.get('hostname', '?'))}",
        f"<b>{_('Timezone', lang)}:</b> "
        f"{escape(shared.get('timezone', '?'))}",
    ]

    recommended_method = _recommended_input_method(shared)
    if recommended_method is not None:
        lines.insert(
            2,
            f"<b>{escape(recommended_method.display_name)}:</b> "
            + (
                _("download and install", lang)
                if shared.get("install_input_method", True)
                else _("do not install", lang)
            ),
        )

    if guided_mode:
        if not isinstance(guided_preview, GuidedStoragePreview):
            platform_error = platform_error or _(
                "Guided storage selection is missing. Rescan and select the "
                "target again.",
                lang,
            )
        else:
            confirmation = build_guided_storage_confirmation(guided_preview)
            preserved_paths = ", ".join(confirmation.preserved_paths)
            created_partitions = ", ".join(
                _("{name}: {start}–{end} MiB", lang).format(
                    name=item.name,
                    start=item.start_mib,
                    end=item.end_mib,
                )
                for item in confirmation.new_partitions
            )
            formatted_paths = ", ".join(
                _("{path} as {filesystem}", lang).format(
                    path=item.display_path,
                    filesystem=item.filesystem,
                )
                for item in confirmation.formats
            )
            if confirmation.reused_esp_path:
                esp_policy = _(
                    "Reuse {path}; never format it; verify FAT health, "
                    "identity and 64 MiB free before writing.",
                    lang,
                ).format(
                    path=confirmation.reused_esp_path
                )
            else:
                esp_policy = _(
                    "Create and format a dedicated AnduinOS EFI System "
                    "Partition inside the selected space.",
                    lang,
                )
            coexistence_lines = [
                f"<b>{_('Storage mode', lang)}:</b> "
                + _("Install alongside", lang),
                f"<b>{_('Selected unallocated space', lang)}:</b> "
                + _("{size} at {offset}", lang).format(
                    size=_human_size(guided_preview.extent.size_bytes),
                    offset=_human_size(guided_preview.extent.start_bytes),
                ),
                f"<b>{_('Preserved partitions', lang)}:</b> "
                + _("{count}: {paths}", lang).format(
                    count=len(confirmation.preserved_paths),
                    paths=escape(preserved_paths),
                ),
                f"<b>{_('New partitions', lang)}:</b> "
                + escape(created_partitions),
                f"<b>{_('Formats', lang)}:</b> "
                + escape(formatted_paths),
                f"<b>{_('EFI policy', lang)}:</b> "
                + escape(esp_policy),
                f"<b>{_('Boot policy', lang)}:</b> "
                + _(
                    "Write only EFI/AnduinOS, do not overwrite EFI/BOOT, "
                    "and require a verified AnduinOS NVRAM entry.",
                    lang,
                ),
            ]
            lines[5:5] = coexistence_lines
    elif platform is not None and swap_sizing is not None:
        layout = build_erase_disk_layout_spec(
            architecture=platform.architecture,
            filesystem=Filesystem(filesystem),
            esp_size_mib=1024,
            swap_size_mib=swap_sizing.swap_size_mib,
        )
        disk_size_mib = int(shared.get("disk_size_bytes") or 0) // MIB
        partition_rows = []
        for item in layout.partitions:
            size_mib = (
                item.size_mib
                if item.size_mib is not None
                else max(0, disk_size_mib - item.start_mib)
            )
            size_text = _human_size(size_mib * MIB)
            if item.end_mib is None:
                size_text = f"≈ {size_text}"
            details = [
                f"#{item.number}",
                item.name,
                size_text,
                item.filesystem or "—",
            ]
            if item.mount_point:
                details.append(f"→ {item.mount_point}")
            elif item.flags:
                details.append(",".join(item.flags))
            partition_rows.append(" · ".join(details))
        lines[5:5] = [
            f"<b>{_('Storage mode', lang)}:</b> "
            + _(
                "Erase every partition and all data on the selected disk.",
                lang,
            ),
            f"<b>{_('New partitions', lang)}:</b>\n"
            + escape("\n".join(partition_rows)),
        ]

    summary_label = Gtk.Label(
        margin_start=48,
        margin_end=48,
        margin_top=24,
        wrap=True,
        xalign=0,
    )
    summary_label.set_markup("\n\n".join(lines))
    summary_card = card()
    summary_card.set_margin_start(48)
    summary_card.set_margin_end(48)
    summary_card.set_margin_top(12)
    summary_card.append(summary_label)
    if swap_sizing is not None:
        swap_gib = swap_sizing.swap_size_mib // 1024
        hibernation_gib = swap_sizing.hibernation_target_mib // 1024
        runtime_gib = swap_sizing.runtime_target_mib // 1024
        budget_gib = swap_sizing.disk_budget_mib // 1024
        swap_details = [
            f"RAM = {_human_size(swap_sizing.physical_memory_bytes)}",
            "swap ≥ 2 GiB",
            "/ ≥ 20 GiB",
            f"swap ≤ {budget_gib} GiB  (disk − ESP − /)",
            (
                f"ceil(RAM) + 1 GiB = {hibernation_gib} GiB  ✓"
                if swap_sizing.hibernation_capacity
                else f"ceil(RAM) + 1 GiB = {hibernation_gib} GiB  ✗"
            ),
        ]
        if not swap_sizing.hibernation_capacity:
            swap_details.append(
                f"ceil(RAM / 2) ≤ 64 GiB = {runtime_gib} GiB  ✓"
            )
        swap_details.append(f"⇒ swap = {swap_gib} GiB")
        swap_explanation = Gtk.Expander(
            label=f"{_('Swap', lang)}: {swap_gib} GiB · ⚙ AUTO ⓘ",
            margin_start=12,
            margin_end=12,
            margin_bottom=8,
        )
        swap_explanation.set_tooltip_text(
            _(
                "4 GiB disk swap (priority 10) + "
                "50% RAM LZ4 zram (priority 100)",
                lang,
            ).replace("4 GiB", f"{swap_gib} GiB", 1)
        )
        detail_label = Gtk.Label(
            xalign=0,
            wrap=True,
            selectable=True,
            margin_start=24,
            margin_top=8,
            margin_bottom=8,
        )
        detail_label.set_markup(
            "<tt>" + escape("\n".join(swap_details)) + "</tt>"
        )
        swap_explanation.set_child(detail_label)
        summary_card.append(swap_explanation)
    summary_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vexpand=True,
    )
    summary_scroll.set_child(clamp_content(summary_card, 860))
    content.append(summary_scroll)

    # Warning
    warning_text = (
        _(
            "⚠ Only the selected unallocated space will be partitioned and "
            "formatted. Existing partitions are preserved, but EFI/AnduinOS "
            "files and the AnduinOS firmware boot entry may change. This "
            "installer will not shrink Windows for you.",
            lang,
        )
        if guided_mode
        else _(
            "⚠ This will erase ALL data on the selected disk. "
            "This action cannot be undone.",
            lang,
        )
    )
    warn = Gtk.Label(label=warning_text, wrap=True)
    warn.add_css_class("warning")
    warn.add_css_class(
        "installer-warning-card"
        if guided_mode
        else "installer-danger-card"
    )
    warn.set_halign(Gtk.Align.CENTER)
    warn.set_margin_top(24)
    content.append(warn)

    install_button = None

    def on_install():
        if platform_error or shared.get("installation_running"):
            return
        assert install_button is not None
        install_button.set_sensitive(False)
        disk = str(shared.get("disk", "?"))
        stable_id = str(shared.get("disk_stable_id", "?"))
        if development_mode:
            confirmation_heading = N_("Validate this installation plan?")
            confirmation_action = N_("Validate Plan (No Installation)")
        elif guided_mode:
            confirmation_heading = N_("Install in the selected free space?")
            confirmation_action = N_("Install Alongside")
        else:
            confirmation_heading = N_("Erase the entire selected disk?")
            confirmation_action = N_("Erase Disk and Install")
        dialog = Adw.MessageDialog(
            transient_for=nav_view.get_root(),
            heading=_(confirmation_heading, lang),
            body=(
                (
                    _(
                        "Development mode will simulate installation to "
                        "{disk}. No disk data will be changed.\n\n",
                        lang,
                    ).format(disk=disk)
                    if development_mode
                    else _(
                        "AnduinOS will use only the selected unallocated "
                        "space on {disk}. Existing partitions will be "
                        "preserved.\n\n",
                        lang,
                    ).format(disk=disk)
                    if guided_mode
                    else _(
                        "All partitions and data on {disk} will be "
                        "destroyed.\n\n",
                        lang,
                    ).format(disk=disk)
                )
                + _("Stable identity: {stable_id}\n\n", lang).format(
                    stable_id=stable_id
                )
                + (
                    _("The privileged executor is disabled.", lang)
                    if development_mode
                    else _(
                        "Existing partitions will be preserved. New AnduinOS "
                        "partitions will be created only in the selected "
                        "unallocated extent; EFI vendor files and the AnduinOS "
                        "firmware boot entry may be updated.",
                        lang,
                    )
                    if guided_mode
                    else _(
                        "This installer does not shrink or preserve other "
                        "systems.",
                        lang,
                    )
                )
            ),
        )
        dialog.add_response("cancel", _("Back", lang))
        dialog.add_response("confirm", _(confirmation_action, lang))
        if not development_mode:
            dialog.set_response_appearance(
                "confirm", Adw.ResponseAppearance.DESTRUCTIVE
            )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _confirmed(_dialog, response):
            if response != "confirm":
                install_button.set_sensitive(True)
                return
            try:
                plan = create_install_plan(shared)
            except Exception as error:
                install_button.set_sensitive(True)
                failure = Adw.MessageDialog(
                    transient_for=nav_view.get_root(),
                    heading=_("Cannot create installation plan", lang),
                    body=str(error),
                )
                failure.add_response("ok", _("OK", lang))
                failure.present()
                return
            shared["installation_running"] = True
            nav_view.push(build_progress_page(plan, shared, nav_view))

        dialog.connect("response", _confirmed)
        dialog.present()

    def on_back():
        nav_view.pop()

    nav = _nav_box(
        lang,
        on_back=on_back,
        on_next=on_install,
        next_label=(
            _("Install Alongside", lang)
            if guided_mode
            else _("Install", lang)
        ),
        next_destructive=not development_mode,
        stage=3,
    )
    install_button = nav.next_button
    install_button.set_sensitive(not bool(platform_error))
    content.append(nav)
    page.set_child(content)
    return page


# ── page 10: Progress / Installation ─────────────────────────────────────

def build_progress_page(plan: InstallPlan, shared, nav_view):
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Installing AnduinOS", lang))
    page.set_tag("progress")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(
        _page_header(
            "Installing AnduinOS",
            "Please do not turn off your computer",
            "timeback",
            lang,
        )
    )

    selected_method = input_method(plan.regional.input_method)
    input_method_title = _("Install input method", lang)
    if selected_method is not None:
        input_method_title += f" · {selected_method.display_name}"
    step_titles = {
        "detect-boot-environment": _(
            "Detect firmware and Secure Boot", lang
        ),
        "detect-network-connectivity": _(
            "Detect Internet connectivity", lang
        ),
        "verify-target-disk": _("Verify target disk isolation", lang),
        "prepare-storage": _("Prepare installation disk", lang),
        "mount-target": _("Mount target filesystems", lang),
        "copy-system": _("Copy AnduinOS system", lang),
        "migrate-wifi-connection": _(
            "Preserve connected Wi-Fi network", lang
        ),
        "configure-storage": _("Configure storage and swap", lang),
        "enter-chroot": _("Prepare target environment", lang),
        "cleanup-live-system": _("Remove live-session components", lang),
        "configure-keyboard-layout": _("Keyboard Layout", lang),
        "install-input-method": input_method_title,
        "configure-system": _(
            "Configure account, region, timezone, and machine identity", lang
        ),
        "select-fastest-apt-mirror": _(
            "Select fastest package mirror", lang
        ),
        "prepare-secure-boot": _("Prepare Secure Boot", lang),
        "install-language-packs": _(
            "Ensure required language packs are installed", lang
        ),
        "refresh-package-indexes": _("Refresh package indexes", lang),
        "upgrade-system": _("Install system updates", lang),
        "ensure-timeback-machine": _(
            "Ensure Timeback Machine is available", lang
        ),
        "install-third-party-drivers": _("Install hardware drivers", lang),
        "verify-dkms-signatures": _(
            "Verify kernel module signatures", lang
        ),
        "install-bootloader": _("Install bootloader", lang),
        "enroll-secure-boot": _("Schedule MOK enrollment", lang),
        "leave-chroot": _("Finalize target environment", lang),
        "unmount-target": _("Unmount installed system", lang),
    }
    step_rows = {}
    step_list = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=3,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
    )
    for step_id, title in step_titles.items():
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        light = Gtk.Label(label="○", width_chars=2)
        light.add_css_class("step-light")
        light.add_css_class("step-pending")
        label = Gtk.Label(label=title, halign=Gtk.Align.START, hexpand=True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        row.set_tooltip_text(title)
        row.append(light)
        row.append(label)
        step_list.append(row)
        step_rows[step_id] = (row, light, label)

    omitted_steps = set()
    if not plan.software.install_updates:
        omitted_steps.update(("refresh-package-indexes", "upgrade-system"))
    if plan.storage.filesystem is not Filesystem.BTRFS:
        omitted_steps.add("ensure-timeback-machine")
    if not plan.software.install_third_party_drivers:
        omitted_steps.add("install-third-party-drivers")
    for step_id in omitted_steps:
        _row, light, _label = step_rows[step_id]
        light.remove_css_class("step-pending")
        light.add_css_class("step-skipped")
        light.set_label("–")

    left_title = Gtk.Label(
        label=_("Installation Steps", lang),
        halign=Gtk.Align.START,
        margin_top=12,
        margin_start=12,
    )
    left_title.add_css_class("heading")
    left_scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vexpand=True,
        min_content_width=285,
    )
    left_scroll.set_child(step_list)
    left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    left_box.append(left_title)
    left_box.append(left_scroll)
    left_frame = Gtk.Frame()
    left_frame.set_child(left_box)
    left_frame.add_css_class("progress-card")

    # Log view
    log_buf = Gtk.TextBuffer()
    log_view = Gtk.TextView(buffer=log_buf, editable=False, monospace=True,
                            margin_start=48, margin_end=48, margin_top=12,
                            vexpand=True)
    log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    log_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True)
    log_scroll.set_child(log_view)
    output_notice = Gtk.Label(
        visible=False,
        wrap=True,
        margin_start=12,
        margin_end=12,
        margin_top=8,
    )
    output_notice.add_css_class("error")
    copy_log_button = Gtk.Button(label=_("Copy Log", lang))
    copy_log_button.connect(
        "clicked", lambda _button: _copy_log(log_buf, content)
    )
    save_log_button = Gtk.Button(label=_("Save Log", lang))
    save_log_button.connect(
        "clicked", lambda _button: _save_log(log_buf)
    )
    output_actions = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=8,
        halign=Gtk.Align.END,
        margin_end=12,
        margin_top=8,
    )
    output_actions.append(copy_log_button)
    output_actions.append(save_log_button)
    output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    output_box.append(output_actions)
    output_box.append(output_notice)
    output_box.append(log_scroll)

    slides = load_slides(lang)
    slide_stack = Gtk.Stack(
        transition_type=Gtk.StackTransitionType.CROSSFADE,
        transition_duration=500,
        vexpand=True,
    )
    for slide in slides:
        slide_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=16,
            margin_bottom=8,
            margin_start=18,
            margin_end=18,
        )
        title = Gtk.Label(
            label=slide.title,
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        title.add_css_class("title-2")
        picture = Gtk.Picture.new_for_filename(str(slide.image))
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_can_shrink(True)
        picture.set_vexpand(True)
        picture.set_size_request(-1, 190)
        body = Gtk.Label(
            label=slide.body,
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=72,
        )
        body.add_css_class("dim-label")
        slide_box.append(title)
        slide_box.append(picture)
        slide_box.append(body)
        slide_stack.add_named(slide_box, slide.key)

    slide_position = {"value": 0}
    dots = Gtk.Label()

    def _show_slide(position):
        position %= len(slides)
        slide_position["value"] = position
        slide_stack.set_visible_child_name(slides[position].key)
        dots.set_label(
            "  ".join(
                "●" if index == position else "○"
                for index in range(len(slides))
            )
        )

    previous = Gtk.Button.new_from_icon_name("go-previous-symbolic")
    previous.set_tooltip_text(_("Previous slide", lang))
    previous.connect(
        "clicked",
        lambda _button: _show_slide(slide_position["value"] - 1),
    )
    following = Gtk.Button.new_from_icon_name("go-next-symbolic")
    following.set_tooltip_text(_("Next slide", lang))
    following.connect(
        "clicked",
        lambda _button: _show_slide(slide_position["value"] + 1),
    )
    slide_controls = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=12,
        halign=Gtk.Align.CENTER,
        margin_bottom=10,
    )
    slide_controls.append(previous)
    slide_controls.append(dots)
    slide_controls.append(following)
    slideshow_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    slideshow_box.append(slide_stack)
    slideshow_box.append(slide_controls)
    _show_slide(0)

    def _advance_slide():
        _show_slide(slide_position["value"] + 1)
        return True

    slide_timer = {"id": GLib.timeout_add_seconds(9, _advance_slide)}

    result_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=16,
        valign=Gtk.Align.CENTER,
        halign=Gtk.Align.CENTER,
        vexpand=True,
        margin_start=32,
        margin_end=32,
    )
    result_box.add_css_class("installer-card")
    result_icon = Gtk.Image(pixel_size=72)
    result_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
    result_label.add_css_class("title-1")
    result_sub = Gtk.Label(
        wrap=True,
        justify=Gtk.Justification.CENTER,
        max_width_chars=72,
    )
    result_sub.add_css_class("dim-label")
    secure_boot_notice = Gtk.Label(
        label=_("After restart, MOKManager will open. Choose Enroll MOK → Continue → Yes, then enter password 123456.", lang),
        visible=False,
        wrap=True,
        justify=Gtk.Justification.CENTER,
        max_width_chars=64,
    )
    secure_boot_notice.add_css_class("warning")
    reboot_label = (
        N_("Restart and Enroll Secure Boot Key")
        if plan.platform.secure_boot is SecureBoot.ENABLED
        else N_("Reboot Now")
    )
    reboot_btn = _nav_btn(
        reboot_label,
        lang,
        lambda: _do_reboot(),
        css_classes=["suggested-action"],
    )
    reboot_btn.set_visible(False)
    result_box.append(result_icon)
    result_box.append(result_label)
    result_box.append(result_sub)
    result_box.append(secure_boot_notice)
    result_box.append(reboot_btn)

    mode_stack = Gtk.Stack(
        transition_type=Gtk.StackTransitionType.CROSSFADE,
        transition_duration=250,
        vexpand=True,
    )
    mode_stack.add_titled(
        slideshow_box, "discover", _("Discover AnduinOS", lang)
    )
    output_page = mode_stack.add_titled(
        output_box, "output", _("Output", lang)
    )
    complete_page = mode_stack.add_titled(
        result_box, "complete", _("Complete", lang)
    )
    complete_page.set_visible(False)
    mode_stack.set_visible_child_name("discover")
    mode_switcher = Gtk.StackSwitcher(
        stack=mode_stack,
        halign=Gtk.Align.CENTER,
        margin_top=8,
        margin_bottom=4,
    )
    right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    right_box.append(mode_switcher)
    right_box.append(mode_stack)
    output_frame = Gtk.Frame()
    output_frame.set_child(right_box)
    output_frame.add_css_class("progress-card")

    workspace = Gtk.Paned(
        orientation=Gtk.Orientation.HORIZONTAL,
        position=330,
        wide_handle=True,
        vexpand=True,
        margin_start=24,
        margin_end=24,
        margin_top=12,
    )
    workspace.set_start_child(left_frame)
    workspace.set_end_child(output_frame)
    workspace.set_resize_start_child(False)
    workspace.set_shrink_start_child(False)
    content.append(workspace)

    progress_status = Gtk.Label(
        label=_("Preparing installation…", lang),
        halign=Gtk.Align.START,
        margin_start=48,
        margin_end=48,
        margin_top=12,
    )
    progress = Gtk.ProgressBar(
        margin_start=48,
        margin_end=48,
        margin_top=6,
        margin_bottom=12,
    )
    progress.set_show_text(True)
    progress.add_css_class("installer-progress")
    content.append(progress_status)
    content.append(progress)
    progress_footer = _nav_box(
        lang,
        on_back=lambda: None,
        on_next=lambda: None,
        stage=4,
        show_back=False,
    )
    progress_footer.next_button.set_visible(False)
    content.append(progress_footer)

    # Log callback (thread-safe via GLib.idle_add)
    def log(msg: str):
        def _append():
            end = log_buf.get_end_iter()
            log_buf.insert(end, msg + "\n")
            # Auto-scroll
            mark = log_buf.get_insert()
            log_view.scroll_to_mark(mark, 0.0, False, 0, 0)
            return False
        GLib.idle_add(_append)

    def on_done(success: bool, error: str = ""):
        def _done():
            shared["installation_running"] = False
            timer_id = slide_timer.pop("id", 0)
            if timer_id:
                GLib.source_remove(timer_id)
            if success:
                progress.set_fraction(1.0)
                progress.set_text("100%")
                progress_status.set_label(_("Installation complete", lang))
                result_icon.set_from_icon_name("emblem-ok-symbolic")
                if shared.get("development_mode"):
                    result_label.set_label(
                        _("Development simulation completed", lang)
                    )
                    detail = _(
                        "The installation plan is valid. No disk, filesystem, "
                        "bootloader, Secure Boot state, or installed system "
                        "was changed.",
                        lang,
                    )
                    if plan.platform.secure_boot is SecureBoot.ENABLED:
                        detail += _(
                            "\n\nA real installation will create a machine-local "
                            "MOK. After reboot, choose Enroll MOK → Continue → "
                            "Yes in MOKManager and enter password 123456.",
                            lang,
                        )
                    result_sub.set_label(detail)
                elif plan.platform.secure_boot is SecureBoot.ENABLED:
                    result_label.set_label(_("Installation Complete", lang))
                    result_sub.set_label(
                        _("Remove the installation media and restart your computer", lang)
                        + _(
                            "\nOn the blue MOKManager screen choose Enroll "
                            "MOK → Continue → Yes, password: 123456",
                            lang,
                        )
                    )
                else:
                    result_label.set_label(_("Installation Complete", lang))
                    result_sub.set_label(_("Remove the installation media and restart your computer", lang))
                secure_boot_notice.set_visible(
                    plan.platform.secure_boot is SecureBoot.ENABLED
                )
                reboot_btn.set_visible(True)
                reboot_btn.set_sensitive(not shared.get("development_mode"))
                if shared.get("development_mode"):
                    reboot_btn.set_tooltip_text(
                        _(
                            "Restart is disabled in development protection "
                            "mode",
                            lang,
                        )
                    )
                complete_page.set_visible(True)
                mode_stack.set_visible_child_name("complete")
            else:
                progress_status.set_label(_("Installation failed", lang))
                output_notice.set_label(
                    f"{_('Installation Failed', lang)}\n{error}"
                )
                output_notice.set_visible(True)
                output_page.set_title(_("Output • Error", lang))
                mode_stack.set_visible_child_name("output")
                log(f"ERROR: {error}")
            return False
        GLib.idle_add(_done)

    def update_progress(step: str, done: int, total: int):
        def _update():
            fraction = 0.0 if total <= 0 else min(1.0, done / total)
            progress.set_fraction(fraction)
            progress.set_text(f"{fraction * 100:.0f}%")
            if step == "complete":
                progress_status.set_label(_("Installation complete", lang))
            else:
                progress_status.set_label(
                    step_titles.get(step, step.replace("-", " ").title())
                )
            return False
        GLib.idle_add(_update)

    status_symbols = {
        "pending": "○",
        "running": "●",
        "succeeded": "✓",
        "warning": "!",
        "failed": "×",
        "skipped": "–",
    }
    warning_count = {"value": 0}

    def update_step_status(step: str, status: str, message: str):
        def _update():
            widgets = step_rows.get(step)
            if widgets is None:
                return False
            row, light, label = widgets
            for name in status_symbols:
                light.remove_css_class(f"step-{name}")
            light.add_css_class(
                f"step-{status}" if status in status_symbols else "step-pending"
            )
            light.set_label(status_symbols.get(status, "○"))
            if status == "running":
                label.add_css_class("step-active")
            else:
                label.remove_css_class("step-active")
            row.set_tooltip_text(message or step_titles.get(step, step))
            if status == "warning":
                warning_count["value"] += 1
                output_page.set_title(
                    _("Output • {count} warning(s)", lang).format(
                        count=warning_count["value"]
                    )
                )
            elif status == "failed":
                output_page.set_title(_("Output • Error", lang))
                output_notice.set_label(
                    message
                    or _("{step} failed", lang).format(
                        step=step_titles.get(step, step)
                    )
                )
                output_notice.set_visible(True)
                mode_stack.set_visible_child_name("output")
            return False
        GLib.idle_add(_update)

    def execute():
        client = (
            DevelopmentExecutorClient()
            if shared.get("development_mode")
            else ExecutorClient()
        )
        success, error = client.run(
            plan, log, update_progress, update_step_status
        )
        on_done(success, error)

    # Run the privileged helper in a background thread.
    thread = threading.Thread(target=execute, daemon=True)
    thread.start()

    page.set_child(content)
    return page


def _do_reboot():
    """Reboot the system."""
    try:
        import subprocess
        subprocess.run(["reboot"], timeout=5)
    except Exception:
        pass


def _save_log(log_buf):
    """Save the install log to the current live user's home directory."""
    try:
        text = log_buf.get_text(
            log_buf.get_start_iter(), log_buf.get_end_iter(), False)
        dest = os.path.join(os.path.expanduser("~"), "anduinos-install.log")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _copy_log(log_buf, widget):
    """Copy the complete installer output to the desktop clipboard."""
    text = log_buf.get_text(
        log_buf.get_start_iter(), log_buf.get_end_iter(), False
    )
    widget.get_clipboard().set(text)


# ── page 11: Done (standalone, for future use) ───────────────────────────

def build_done_page(shared, nav_view):
    """Simple post-install page. Currently unused — progress page handles both states."""
    lang = shared.get("lang", DEFAULT_LANGUAGE)
    page = Adw.NavigationPage(title=_("Installation Complete", lang))
    page.set_tag("done")
    page.set_child(Gtk.Label(label=_("Installation Complete", lang)))
    return page


# ── build all pages (called from main.py) ─────────────────────────────────

def build_all_pages(shared: dict, nav_view: Adw.NavigationView):
    """Return a list of all pages. The first page is the entry point."""
    return [
        build_welcome_page(shared, nav_view),
        # Other pages are pushed on demand — we only pre-build the first one.
    ]
