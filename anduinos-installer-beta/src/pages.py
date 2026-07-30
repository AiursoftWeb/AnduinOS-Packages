"""Wizard pages for the AnduinOS GTK4 installer.

Each page is built by a function that returns an Adw.NavigationPage.
Pages communicate through a shared state dict.

Navigation: each page gets a reference to the Adw.NavigationView so it
can push the next page when the user clicks "Next" / "Install".
"""

import threading
import re
import html

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
    LANGUAGES,
    default_timezone,
    is_chinese,
    Language as LangData,
)
from i18n import _, N_
from frontend import (
    DevelopmentExecutorClient,
    ExecutorClient,
    create_install_plan,
)
from installer_core.btrfs import BTRFS_SUBVOLUMES
from installer_core.account_security import AccountNextAction, account_next_action
from installer_core.model import InstallPlan, SecureBoot
from installer_core.probe import ProbeError, probe_disks, probe_platform
from slideshow import load_slides


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


class DiskItem(GObject.Object):
    """GObject wrapper for a disk device entry."""
    __gtype_name__ = "DiskItem"
    devname = GObject.Property(type=str)
    size = GObject.Property(type=str)
    model = GObject.Property(type=str)
    sensitive = GObject.Property(type=bool, default=True)
    subtitle = GObject.Property(type=str)
    size_bytes = GObject.Property(type=str)
    stable_id = GObject.Property(type=str)

    def __init__(self, devname: str, size: str, model: str,
                 sensitive: bool = True, subtitle: str = "",
                 size_bytes: int = 0, stable_id: str = ""):
        super().__init__()
        self.devname = devname
        self.size = size
        self.model = model
        self.sensitive = sensitive
        self.subtitle = subtitle
        self.size_bytes = str(size_bytes)
        self.stable_id = stable_id


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
             next_sensitive=True, next_destructive=False):
    """Standard bottom navigation bar with Back / Next buttons."""
    box = Gtk.Box(spacing=12, homogeneous=False, margin_top=24,
                  margin_bottom=12, margin_start=24, margin_end=24)
    box.set_halign(Gtk.Align.CENTER)

    back = _nav_btn("Back", lang, on_back)
    box.append(back)

    css = ["destructive-action"] if next_destructive else ["suggested-action"]
    nxt = _nav_btn(next_label, lang, on_next,
                   sensitive=next_sensitive, css_classes=css)
    box.append(nxt)
    return box


# ── page 1: Welcome / Language selection ─────────────────────────────────

def build_welcome_page(shared, nav_view):
    """Language list on the left, native GTK4 welcome panel on the right."""
    lang = shared.get("lang", "en_US")
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
        row = Adw.ActionRow()
        item.set_child(row)

    def _on_bind(_f, item):
        row = item.get_child()
        lang_item = item.get_item()
        row.set_title(lang_item.native)
        row.set_subtitle(lang_item.english)

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

    # ── right: native GTK4 welcome panel ──
    right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=24, vexpand=True, hexpand=True,
                        halign=Gtk.Align.CENTER,
                        valign=Gtk.Align.CENTER)

    # AnduinOS logo / icon
    welcome_icon = Gtk.Image.new_from_icon_name("anduinos-installer-beta")
    welcome_icon.set_pixel_size(128)
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

    right_frame = Gtk.Frame()
    right_frame.set_child(right_box)

    # ── layout ──
    hpaned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                        position=340, wide_handle=True, vexpand=True)
    hpaned.set_start_child(lang_frame)
    hpaned.set_end_child(right_frame)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page_title = _page_title("Welcome to AnduinOS", lang)
    page_subtitle = _page_subtitle(
        "Choose your language to begin installation", lang
    )
    content.append(page_title)
    content.append(page_subtitle)
    content.append(hpaned)

    # ── handlers ──
    sel = lang_list.get_model()
    navigation_widgets = {}

    def _update_welcome(lang_code: str):
        welcome_title.set_label(_("Welcome to AnduinOS", lang_code))
        welcome_desc.set_label(
            _("Choose your language to begin installation", lang_code)
        )
        page_title.set_label(_("Welcome to AnduinOS", lang_code))
        page_subtitle.set_label(
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
            Gtk.Widget.set_default_direction(
                Gtk.TextDirection.RTL
                if l.code == "ar"
                else Gtk.TextDirection.LTR
            )
            _update_welcome(l.code)
            set_window_language = shared.get("_set_window_language")
            if callable(set_window_language):
                set_window_language(l.code)

    sel.connect("selection-changed", lambda _s, _p, _n: _on_lang_selected())

    def on_next():
        try:
            nav_view.push(build_keyboard_page(shared, nav_view))
        except Exception as e:
            import traceback
            traceback.print_exc()
            selected_lang = str(shared.get("lang", "en_US"))
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
    )
    navigation_widgets["back"] = navigation.get_first_child()
    navigation_widgets["next"] = navigation.get_last_child()
    content.append(navigation)

    # Select the language detected from the Live session. The shared state is
    # initialized before this page is built, so regional defaults stay atomic.
    initial_language = str(shared.get("lang", "en_US"))
    for i, l in enumerate(lang_items):
        if l.code == initial_language:
            lang_list.get_model().select_item(i, True)
            break
    _update_welcome(initial_language)

    page.set_child(content)
    return page


# ── page 2: Keyboard layout ──────────────────────────────────────────────

# Common XKB variants, grouped by region
XKB_VARIANTS = [
    # Latin
    ("us", N_("English (US)")), ("gb", N_("English (UK)")),
    ("de", N_("German")), ("fr", N_("French")), ("it", N_("Italian")),
    ("es", N_("Spanish")), ("pt", N_("Portuguese")),
    ("br", N_("Portuguese (Brazil)")), ("dk", N_("Danish")),
    ("se", N_("Swedish")), ("no", N_("Norwegian")),
    ("fi", N_("Finnish")), ("nl", N_("Dutch")), ("pl", N_("Polish")),
    ("ro", N_("Romanian")),
    # Cyrillic / Greek
    ("ru", N_("Russian")), ("ua", N_("Ukrainian")), ("gr", N_("Greek")),
    # CJK / Indic / Other physical layouts. Chinese input is an input method
    # layered over the US layout, not a separate physical keyboard layout.
    ("jp", N_("Japanese")), ("kr", N_("Korean")),
    ("in", N_("Hindi (India)")), ("th", N_("Thai")),
    ("vn", N_("Vietnamese")), ("ara", N_("Arabic")),
    ("tr", N_("Turkish")), ("id", N_("Indonesian")),
]


def build_keyboard_page(shared, nav_view):
    lang = shared.get("lang", "en_US")
    keyboard = shared.get("keyboard", "us")
    page = Adw.NavigationPage(title=_("Keyboard Layout", lang))
    page.set_tag("keyboard")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("Keyboard Layout", lang))
    content.append(_page_subtitle("Confirm your keyboard layout", lang))

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

    kbd_dropdown = Gtk.DropDown(model=kbd_store,
                                margin_start=48, margin_end=48,
                                margin_top=24)
    kbd_dropdown.set_selected(default_idx)

    def _on_kbd_changed(dd, _pspec):
        idx = dd.get_selected()
        if 0 <= idx < len(XKB_VARIANTS):
            shared["keyboard"] = XKB_VARIANTS[idx][0]

    kbd_dropdown.connect("notify::selected", _on_kbd_changed)

    # Test entry
    test_entry = Gtk.Entry(placeholder_text=_("Test your keyboard here…", lang),
                           margin_top=24, margin_start=48, margin_end=48)

    content.append(kbd_dropdown)
    content.append(test_entry)

    def on_next():
        nav_view.push(build_software_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    content.append(_nav_box(lang, on_back=on_back, on_next=on_next))
    page.set_child(content)
    return page


# ── page 3: Updates and drivers ─────────────────────────────────────────

def build_software_page(shared, nav_view):
    lang = shared.get("lang", "en_US")
    page = Adw.NavigationPage(title=_("Updates and Drivers", lang))
    page.set_tag("software")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("Updates and Drivers", lang))
    content.append(_page_subtitle("Choose optional software to install", lang))

    options = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_start=48,
        margin_end=48,
        margin_top=32,
        vexpand=True,
    )

    updates = Gtk.CheckButton(label=_("Download and install system updates during installation", lang))
    updates.set_active(bool(shared.get("install_updates", True)))
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
    drivers.set_active(
        bool(shared.get("install_third_party_drivers", False))
    )
    drivers_detail = Gtk.Label(
        label=_("Some drivers are proprietary or otherwise non-free software.", lang),
        halign=Gtk.Align.START,
        wrap=True,
        margin_start=28,
    )
    drivers_detail.add_css_class("dim-label")
    options.append(drivers)
    options.append(drivers_detail)
    content.append(options)

    def _save():
        shared["install_updates"] = updates.get_active()
        shared["install_third_party_drivers"] = drivers.get_active()

    updates.connect("toggled", lambda _button: _save())
    drivers.connect("toggled", lambda _button: _save())

    def on_next():
        _save()
        nav_view.push(build_disk_page(shared, nav_view))

    def on_back():
        _save()
        nav_view.pop()

    content.append(_nav_box(lang, on_back=on_back, on_next=on_next))
    page.set_child(content)
    return page


# ── page 4: Disk selection ───────────────────────────────────────────────

def build_disk_page(shared, nav_view):
    lang = shared.get("lang", "en_US")
    page = Adw.NavigationPage(title=_("Select Installation Disk", lang))
    page.set_tag("disk")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("Select Installation Disk", lang))
    content.append(_page_subtitle("Choose the disk where AnduinOS will be installed", lang))

    # Disk list
    list_store = Gio.ListStore(item_type=DiskItem)

    factory = Gtk.SignalListItemFactory()
    def _disk_setup(_f, item):
        row = Adw.ActionRow()
        item.set_child(row)

    def _disk_bind(_f, item):
        row = item.get_child()
        d = item.get_item()
        row.set_title(d.devname)
        row.set_subtitle(d.subtitle)
        row.set_sensitive(d.sensitive)

    factory.connect("setup", _disk_setup)
    factory.connect("bind", _disk_bind)

    disk_list = Gtk.ListView(
        model=Gtk.SingleSelection(model=list_store),
        factory=factory,
        vexpand=True,
    )

    disk_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     margin_start=48, margin_end=48,
                                     vexpand=True)
    disk_scroll.set_child(disk_list)

    filesystem_box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=12,
        halign=Gtk.Align.CENTER,
        margin_top=12,
    )
    filesystem_box.append(Gtk.Label(label=_("Filesystem", lang)))
    filesystem_names = Gtk.StringList.new(
        [_("Btrfs (recommended)", lang), "ext4"]
    )
    filesystem = Gtk.DropDown(model=filesystem_names)
    filesystem.set_selected(
        1 if shared.get("filesystem") == "ext4" else 0
    )
    filesystem.connect(
        "notify::selected",
        lambda widget, _pspec: shared.__setitem__(
            "filesystem", "ext4" if widget.get_selected() == 1 else "btrfs"
        ),
    )
    filesystem_box.append(filesystem)

    # Warning labels
    warn_label = Gtk.Label(label=_("ALL DATA on the selected disk will be permanently erased.", lang))
    warn_label.add_css_class("warning")
    warn_label.set_margin_top(12)
    warn_label.set_halign(Gtk.Align.CENTER)

    content.append(disk_scroll)
    content.append(filesystem_box)
    content.append(warn_label)

    # Populate disks
    try:
        live_dev = _find_live_device()
        for disk in probe_disks():
            size = _human_size(disk.expected_size_bytes)
            is_live = disk.path == live_dev
            sub = f"{size} — {disk.model}"
            if is_live:
                sub += f" {_('(Live USB — excluded)', lang)}"
            list_store.append(DiskItem(
                devname=disk.path, size=size, model=disk.model,
                sensitive=not is_live, subtitle=sub,
                size_bytes=disk.expected_size_bytes,
                stable_id=disk.stable_id,
            ))
        if list_store.get_n_items() == 0:
            list_store.append(DiskItem(
                devname="", size="", model="",
                sensitive=False, subtitle=_("No suitable disks found.", lang),
            ))
    except ProbeError:
        list_store.append(DiskItem(
            devname="", size="", model="",
            sensitive=False, subtitle=_("No suitable disks found.", lang),
        ))

    sel = disk_list.get_model()
    nxt_enabled = False
    next_button = None

    def _on_disk_selected():
        nonlocal nxt_enabled
        pos = sel.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            d = list_store.get_item(pos)
            if d.sensitive and d.devname:
                shared["disk"] = d.devname
                shared["disk_size"] = d.size
                shared["disk_size_bytes"] = int(d.size_bytes)
                shared["disk_model"] = d.model
                shared["disk_stable_id"] = d.stable_id
                nxt_enabled = True
                if next_button is not None:
                    next_button.set_sensitive(True)
                return
        nxt_enabled = False
        if next_button is not None:
            next_button.set_sensitive(False)

    sel.connect("selection-changed", lambda _s, _p, _n: _on_disk_selected())

    def on_next():
        nonlocal nxt_enabled
        _on_disk_selected()
        if nxt_enabled:
            nav_view.push(build_user_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    nav = _nav_box(
        lang, on_back=on_back, on_next=on_next, next_sensitive=nxt_enabled
    )
    next_button = nav.get_last_child()
    # Gtk.SingleSelection selects the first row while the model is populated,
    # before our selection-changed handler is connected. Synchronize that
    # initial selection explicitly so a one-disk machine can continue.
    _on_disk_selected()
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


# ── page 4: User account ─────────────────────────────────────────────────

def build_user_page(shared, nav_view):
    lang = shared.get("lang", "en_US")
    page = Adw.NavigationPage(title=_("User Account", lang))
    page.set_tag("user")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("User Account", lang))
    content.append(_page_subtitle("Create your user account", lang))

    # Validation state
    valid = {"name": True, "pass": True, "host": True}

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                  spacing=12, margin_start=48, margin_end=48,
                  margin_top=24, vexpand=True)

    # Full name
    full_entry = Gtk.Entry(placeholder_text=_("Full Name", lang))
    box.append(_labeled(_("Full Name", lang), full_entry))

    # Username
    user_entry = Gtk.Entry(placeholder_text=_("Username", lang))
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

    # Auto-transliterate full name → username
    def _on_full_changed(entry):
        full = entry.get_text()
        shared["full_name"] = full
        if not user_entry.get_text():
            # Simple ASCII transliteration
            username = _transliterate(full)
            user_entry.set_text(username)

    full_entry.connect("changed", _on_full_changed)

    # Validate on change
    import re
    NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")
    HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")

    def _validate():
        uname = user_entry.get_text()
        pword = pass_entry.get_text()
        confirmation = confirm_entry.get_text()
        host = host_entry.get_text()

        if uname and not NAME_RE.match(uname):
            name_warn.set_label(_("Username may only contain lowercase letters, digits, underscores and hyphens.", lang))
            name_warn.set_visible(True)
            valid["name"] = False
        else:
            name_warn.set_visible(False)
            valid["name"] = bool(uname)

        if not pword and not confirmation:
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

    content.append(box)

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
        dialog = Adw.MessageDialog(
            transient_for=nav_view.get_root(),
            heading=_(
                "This configuration is very unsafe"
                if passwordless
                else "Passwordless sudo is unsafe",
                lang,
            ),
            body=_(
                "The system will sign in to this account automatically and allow it to obtain full administrator privileges without a password. Anyone with physical access, and any program running as this user, can completely control the system. Use this only for a kiosk, temporary virtual machine, or another controlled environment."
                if passwordless
                else "Any program running as your user can obtain full administrator privileges without authentication. Your login password still protects sign-in, but it will not protect sudo. Are you sure you want to continue?",
                lang,
            ),
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

    nav = _nav_box(lang, on_back=on_back, on_next=on_next, next_sensitive=False)
    nxt_btn = nav.get_last_child()  # The "Next" button (last in the box)
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


def _transliterate(full_name: str) -> str:
    """Rough ASCII transliteration for auto-username generation."""
    translit = {
        "à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a", "å": "a",
        "æ": "ae", "ç": "c", "è": "e", "é": "e", "ê": "e", "ë": "e",
        "ì": "i", "í": "i", "î": "i", "ï": "i", "ð": "d", "ñ": "n",
        "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ö": "o", "ø": "o",
        "ù": "u", "ú": "u", "û": "u", "ü": "u", "ý": "y", "þ": "th",
        "ß": "ss", "ā": "a", "ē": "e", "ī": "i", "ū": "u", "ő": "o",
        "ű": "u",
    }
    name = full_name.lower().strip()
    # Apply transliteration table
    for k, v in translit.items():
        name = name.replace(k, v)
    # Remove anything that isn't a-z, 0-9, space, hyphen, underscore
    import re
    name = re.sub(r"[^a-z0-9 _-]", "", name)
    # Collapse spaces and separators into a single separator
    name = re.sub(r"[ _-]+", "-", name).strip("-")
    return name or "user"


# ── page 5: Timezone ─────────────────────────────────────────────────────

def build_timezone_page(shared, nav_view):
    lang = shared.get("lang", "en_US")
    page = Adw.NavigationPage(title=_("Select Timezone", lang))
    page.set_tag("timezone")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("Select Timezone", lang))
    content.append(_page_subtitle("Choose your location to set the system clock", lang))

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
        row = Adw.ActionRow()
        item.set_child(row)
    def _tz_bind(_f, item):
        row = item.get_child()
        row.set_title(item.get_item().get_string())
    factory.connect("setup", _tz_setup)
    factory.connect("bind", _tz_bind)

    tz_list = Gtk.ListView(model=Gtk.SingleSelection(model=filter_model),
                           factory=factory, vexpand=True)
    tz_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                   margin_start=48, margin_end=48,
                                   vexpand=True)
    tz_scroll.set_child(tz_list)

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

    content.append(_nav_box(lang, on_back=on_back, on_next=on_next))
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


# ── page 6: Summary ──────────────────────────────────────────────────────

def build_summary_page(shared, nav_view):
    lang = shared.get("lang", "en_US")
    page = Adw.NavigationPage(title=_("Ready to Install", lang))
    page.set_tag("summary")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("Ready to Install", lang))
    content.append(_page_subtitle("Please review your choices before proceeding", lang))
    development_mode = bool(shared.get("development_mode"))
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
        content.append(development_banner)

    # Build summary text
    lang_name = "English"
    for l in LANGUAGES:
        if l.code == shared.get("lang"):
            lang_name = f"{l.english_name} ({l.native_name})"
            break

    secure_boot_enabled = False
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
        f"<b>{_('Swap', lang)}:</b> "
        + _(
            "4 GiB disk swap (priority 10) + "
            "50% RAM LZ4 zram (priority 100)",
            lang,
        ),
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

    summary_label = Gtk.Label(
        margin_start=48, margin_end=48, margin_top=24, vexpand=True,
    )
    summary_label.set_markup("\n\n".join(lines))
    content.append(summary_label)

    # Warning
    warn = Gtk.Label(label=_("⚠ This will erase ALL data on the selected disk. This action cannot be undone.", lang))
    warn.add_css_class("warning")
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
        dialog = Adw.MessageDialog(
            transient_for=nav_view.get_root(),
            heading=_(
                (
                    "Validate this installation plan?"
                    if development_mode
                    else "Erase the entire selected disk?"
                ),
                lang,
            ),
            body=(
                (
                    _(
                        "Development mode will simulate installation to "
                        "{disk}. No disk data will be changed.\n\n",
                        lang,
                    ).format(disk=disk)
                    if development_mode
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
                        "This installer does not shrink or preserve other "
                        "systems.",
                        lang,
                    )
                )
            ),
        )
        dialog.add_response("cancel", _("Back", lang))
        dialog.add_response(
            "erase",
            _(
                (
                    "Validate Plan (No Installation)"
                    if development_mode
                    else "Erase Disk and Install"
                ),
                lang,
            ),
        )
        if not development_mode:
            dialog.set_response_appearance(
                "erase", Adw.ResponseAppearance.DESTRUCTIVE
            )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _confirmed(_dialog, response):
            if response != "erase":
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
        next_label=_("Install", lang),
        next_destructive=True,
    )
    install_button = nav.get_last_child()
    install_button.set_sensitive(not bool(platform_error))
    content.append(nav)
    page.set_child(content)
    return page


# ── page 7: Progress / Installation ──────────────────────────────────────

def build_progress_page(plan: InstallPlan, shared, nav_view):
    lang = shared.get("lang", "en_US")
    page = Adw.NavigationPage(title=_("Installing AnduinOS", lang))
    page.set_tag("progress")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("Installing AnduinOS", lang))
    content.append(_page_subtitle("Please do not turn off your computer", lang))

    css = Gtk.CssProvider()
    css.load_from_data(
        ".step-light { font-size: 18px; font-weight: 700; }"
        ".step-pending { color: alpha(@window_fg_color, 0.28); }"
        ".step-running { color: #3584e4; }"
        ".step-succeeded { color: #2ec27e; }"
        ".step-warning { color: #e5a50a; }"
        ".step-failed { color: #e01b24; }"
        ".step-skipped { color: alpha(@window_fg_color, 0.45); }"
        ".step-active { font-weight: 700; }"
    )
    Gtk.StyleContext.add_provider_for_display(
        content.get_display(),
        css,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

    step_titles = {
        "detect-boot-environment": _(
            "Detect firmware and Secure Boot", lang
        ),
        "verify-target-disk": _("Verify target disk isolation", lang),
        "prepare-storage": _("Prepare installation disk", lang),
        "mount-target": _("Mount target filesystems", lang),
        "copy-system": _("Copy AnduinOS system", lang),
        "configure-storage": _("Configure storage and swap", lang),
        "enter-chroot": _("Prepare target environment", lang),
        "cleanup-live-system": _("Remove live-session components", lang),
        "configure-system": _("Configure account and region", lang),
        "select-fastest-apt-mirror": _(
            "Select fastest package mirror", lang
        ),
        "prepare-secure-boot": _("Prepare Secure Boot", lang),
        "refresh-package-indexes": _("Refresh package indexes", lang),
        "upgrade-system": _("Install system updates", lang),
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
    reboot_btn = _nav_btn(
        _(
            (
                "Restart and Enroll Secure Boot Key"
                if plan.platform.secure_boot is SecureBoot.ENABLED
                else "Reboot Now"
            ),
            lang,
        ),
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
    content.append(progress_status)
    content.append(progress)

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


# ── page 8: Done (standalone, for future use) ────────────────────────────

def build_done_page(shared, nav_view):
    """Simple post-install page. Currently unused — progress page handles both states."""
    lang = shared.get("lang", "en_US")
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
