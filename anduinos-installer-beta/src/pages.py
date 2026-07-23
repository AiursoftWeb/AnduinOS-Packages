"""Wizard pages for the AnduinOS GTK4 installer.

Each page is built by a function that returns an Adw.NavigationPage.
Pages communicate through a shared state dict.

Navigation: each page gets a reference to the Adw.NavigationView so it
can push the next page when the user clicks "Next" / "Install".
"""

import threading
import re

# Allow absolute imports when run directly (not as a package).
import sys, os
_install_dir = os.path.dirname(os.path.abspath(__file__))
if _install_dir not in sys.path:
    sys.path.insert(0, _install_dir)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio, Pango, GObject

from languages import LANGUAGES, _, is_chinese, Language as LangData
from backend import Installer


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

    def __init__(self, devname: str, size: str, model: str,
                 sensitive: bool = True, subtitle: str = ""):
        super().__init__()
        self.devname = devname
        self.size = size
        self.model = model
        self.sensitive = sensitive
        self.subtitle = subtitle


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


def _nav_box(lang, on_back, on_next, next_label="nav.next",
             next_sensitive=True, next_destructive=False):
    """Standard bottom navigation bar with Back / Next buttons."""
    box = Gtk.Box(spacing=12, homogeneous=False, margin_top=24,
                  margin_bottom=12, margin_start=24, margin_end=24)
    box.set_halign(Gtk.Align.CENTER)

    back = _nav_btn("nav.back", lang, on_back)
    box.append(back)

    css = ["destructive-action"] if next_destructive else ["suggested-action"]
    nxt = _nav_btn(next_label, lang, on_next,
                   sensitive=next_sensitive, css_classes=css)
    box.append(nxt)
    return box


# ── page 1: Welcome / Language selection ─────────────────────────────────

def build_welcome_page(shared, nav_view):
    """Language list on the left, native GTK4 welcome panel on the right."""
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title="AnduinOS Installer")
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
    welcome_icon = Gtk.Image.new_from_icon_name("computer-symbolic")
    welcome_icon.set_pixel_size(128)
    welcome_icon.add_css_class("dim-label")
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
    content.append(_page_title("welcome.title", lang))
    content.append(_page_subtitle("welcome.subtitle", lang))
    content.append(hpaned)

    # ── handlers ──
    sel = lang_list.get_model()

    def _update_welcome(lang_code: str):
        welcome_title.set_label(_("welcome.title", lang_code))
        welcome_desc.set_label(_("welcome.subtitle", lang_code))

    def _on_lang_selected():
        pos = sel.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            l = lang_items[pos]
            shared["lang"] = l.code
            shared["keyboard"] = l.keyboard
            shared["timezone"] = _guess_timezone(l.code)
            _update_welcome(l.code)

    sel.connect("selection-changed", lambda _s, _p, _n: _on_lang_selected())

    # Default selection: find English
    for i, l in enumerate(lang_items):
        if l.code == "en":
            lang_list.get_model().select_item(i, True)
            break
    _update_welcome("en")

    def on_next():
        try:
            nav_view.push(build_keyboard_page(shared, nav_view))
        except Exception as e:
            import traceback
            traceback.print_exc()
            dlg = Adw.MessageDialog(
                transient_for=nav_view.get_root(),
                heading="Navigation error",
                body=str(e),
            )
            dlg.add_response("ok", "OK")
            dlg.present()

    content.append(_nav_box(lang,
                            on_back=lambda: None,
                            on_next=on_next))
    page.set_child(content)
    return page


# ── page 2: Keyboard layout ──────────────────────────────────────────────

# Common XKB variants, grouped by region
XKB_VARIANTS = [
    # Latin
    ("us", "English (US)"), ("gb", "English (UK)"), ("de", "German"),
    ("fr", "French"), ("it", "Italian"), ("es", "Spanish"),
    ("pt", "Portuguese"), ("br", "Portuguese (Brazil)"),
    ("dk", "Danish"), ("se", "Swedish"), ("no", "Norwegian"),
    ("fi", "Finnish"), ("nl", "Dutch"), ("pl", "Polish"),
    ("ro", "Romanian"),
    # Cyrillic / Greek
    ("ru", "Russian"), ("ua", "Ukrainian"), ("gr", "Greek"),
    # CJK / Indic / Other
    ("cn", "Chinese"), ("tw", "Chinese (Taiwan)"), ("hk", "Chinese (Hong Kong)"),
    ("jp", "Japanese"), ("kr", "Korean"),
    ("in", "Hindi (India)"), ("th", "Thai"), ("vn", "Vietnamese"),
    ("ara", "Arabic"), ("tr", "Turkish"), ("id", "Indonesian"),
]


def build_keyboard_page(shared, nav_view):
    lang = shared.get("lang", "en")
    keyboard = shared.get("keyboard", "us")
    page = Adw.NavigationPage(title=_("keyboard.title", lang))
    page.set_tag("keyboard")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("keyboard.title", lang))
    content.append(_page_subtitle("keyboard.subtitle", lang))

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
        kbd_store.append(name)

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
    test_entry = Gtk.Entry(placeholder_text=_("keyboard.test", lang),
                           margin_top=24, margin_start=48, margin_end=48)

    content.append(kbd_dropdown)
    content.append(test_entry)

    def on_next():
        nav_view.push(build_disk_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    content.append(_nav_box(lang, on_back=on_back, on_next=on_next))
    page.set_child(content)
    return page


# ── page 3: Disk selection ───────────────────────────────────────────────

def build_disk_page(shared, nav_view):
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title=_("disk.title", lang))
    page.set_tag("disk")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("disk.title", lang))
    content.append(_page_subtitle("disk.subtitle", lang))

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

    # Warning labels
    warn_label = Gtk.Label(label=_("disk.warning_erase", lang))
    warn_label.add_css_class("warning")
    warn_label.set_margin_top(12)
    warn_label.set_halign(Gtk.Align.CENTER)

    content.append(disk_scroll)
    content.append(warn_label)

    # Populate disks
    import subprocess
    try:
        out = subprocess.check_output(
            ["lsblk", "-dno", "NAME,SIZE,MODEL,TYPE,TRAN"],
            text=True, timeout=5,
        )
        live_dev = _find_live_device()

        for line in out.strip().split("\n"):
            parts = line.split(maxsplit=4)
            if len(parts) < 3:
                continue
            name, size, model = parts[0], parts[1], parts[2]
            dev_type = parts[3] if len(parts) > 3 else ""
            if dev_type != "disk":
                continue
            devname = f"/dev/{name}"
            is_live = devname == live_dev
            sub = f'{size} — {model}'
            if is_live:
                sub += f" {_('disk.live_usb', lang)}"
            list_store.append(DiskItem(
                devname=devname, size=size, model=model,
                sensitive=not is_live, subtitle=sub,
            ))
    except Exception:
        list_store.append(DiskItem(
            devname="", size="", model="",
            sensitive=False, subtitle=_("disk.no_disks", lang),
        ))

    sel = disk_list.get_model()
    nxt_enabled = False

    def _on_disk_selected():
        nonlocal nxt_enabled
        pos = sel.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            d = list_store.get_item(pos)
            if d.sensitive and d.devname:
                shared["disk"] = d.devname
                shared["disk_size"] = d.size
                shared["disk_model"] = d.model
                nxt_enabled = True
                return
        nxt_enabled = False

    sel.connect("selection-changed", lambda _s, _p, _n: _on_disk_selected())

    def on_next():
        nonlocal nxt_enabled
        _on_disk_selected()
        if nxt_enabled:
            nav_view.push(build_user_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    content.append(_nav_box(lang, on_back=on_back, on_next=on_next,
                            next_sensitive=nxt_enabled))
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
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title=_("user.title", lang))
    page.set_tag("user")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("user.title", lang))
    content.append(_page_subtitle("user.subtitle", lang))

    # Validation state
    valid = {"name": True, "pass": True, "host": True}

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                  spacing=12, margin_start=48, margin_end=48,
                  margin_top=24, vexpand=True)

    # Full name
    full_entry = Gtk.Entry(placeholder_text=_("user.full_name", lang))
    box.append(_labeled(_("user.full_name", lang), full_entry))

    # Username
    user_entry = Gtk.Entry(placeholder_text=_("user.username", lang))
    name_warn = Gtk.Label(visible=False)
    name_warn.add_css_class("warning")
    box.append(_labeled(_("user.username", lang), user_entry))
    box.append(name_warn)

    # Password
    pass_entry = Gtk.Entry(placeholder_text=_("user.password", lang),
                           visibility=False)
    pass_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    pass_warn = Gtk.Label(visible=False)
    pass_warn.add_css_class("warning")

    show_toggle = Gtk.CheckButton(label=_("user.show_password", lang))
    show_toggle.connect("toggled",
                        lambda b: pass_entry.set_visibility(b.get_active()))

    box.append(_labeled(_("user.password", lang), pass_entry))
    box.append(show_toggle)
    box.append(pass_warn)

    # Hostname
    host_entry = Gtk.Entry(
        placeholder_text=_("user.hostname", lang),
        text=shared.get("hostname", "anduinos"),
    )
    host_warn = Gtk.Label(visible=False)
    host_warn.add_css_class("warning")
    box.append(_labeled(_("user.hostname", lang), host_entry))
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
        host = host_entry.get_text()

        if uname and not NAME_RE.match(uname):
            name_warn.set_label(_("user.name_invalid", lang))
            name_warn.set_visible(True)
            valid["name"] = False
        else:
            name_warn.set_visible(False)
            valid["name"] = bool(uname)

        if pword and len(pword) < 6:
            pass_warn.set_label(_("user.pass_too_short", lang))
            pass_warn.set_visible(True)
            valid["pass"] = False
        else:
            pass_warn.set_visible(False)
            valid["pass"] = bool(pword) and len(pword) >= 6

        if host and not HOST_RE.match(host):
            host_warn.set_label(_("user.host_invalid", lang))
            host_warn.set_visible(True)
            valid["host"] = False
        else:
            host_warn.set_visible(False)
            valid["host"] = bool(host)

        all_valid = valid["name"] and valid["pass"] and valid["host"]
        nxt_btn.set_sensitive(all_valid)

    user_entry.connect("changed", lambda _e: _validate())
    pass_entry.connect("changed", lambda _e: _validate())
    host_entry.connect("changed", lambda _e: _validate())

    content.append(box)

    def on_next():
        shared["username"] = user_entry.get_text()
        shared["password"] = pass_entry.get_text()
        shared["hostname"] = host_entry.get_text()
        nav_view.push(build_timezone_page(shared, nav_view))

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
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title=_("tz.title", lang))
    page.set_tag("timezone")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("tz.title", lang))
    content.append(_page_subtitle("tz.subtitle", lang))

    # Load timezone list
    zones = _load_timezones()

    list_store = Gio.ListStore()
    for z in zones:
        list_store.append(z)

    # Search entry
    search = Gtk.SearchEntry(placeholder_text=_("tz.search", lang),
                             margin_start=48, margin_end=48)

    # Filter model
    filter_model = Gtk.FilterListModel(model=list_store)
    def _filter(item, _user_data):
        query = search.get_text().lower()
        if not query:
            return True
        tz = item.get_item()
        return query in tz.lower()

    filter_model.set_filter(Gtk.CustomFilter.new(_filter))

    factory = Gtk.SignalListItemFactory()
    def _tz_setup(_f, item):
        row = Adw.ActionRow()
        item.set_child(row)
    def _tz_bind(_f, item):
        row = item.get_child()
        row.set_title(item.get_item())
    factory.connect("setup", _tz_setup)
    factory.connect("bind", _tz_bind)

    tz_list = Gtk.ListView(model=Gtk.SingleSelection(model=filter_model),
                           factory=factory, vexpand=True)
    tz_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                   margin_start=48, margin_end=48,
                                   vexpand=True)
    tz_scroll.set_child(tz_list)

    search.connect("search-changed", lambda _s: filter_model.changed(
        Gtk.FilterChange.DIFFERENT))

    # Default selection
    current_tz = shared.get("timezone", "America/New_York")
    for i, z in enumerate(zones):
        if z == current_tz:
            tz_list.get_model().select_item(i, True)
            break

    sel = tz_list.get_model()
    def _on_tz_selected():
        pos = sel.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            shared["timezone"] = filter_model.get_item(pos)

    sel.connect("selection-changed", lambda _s, _p, _n: _on_tz_selected())

    content.append(search)
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


def _guess_timezone(code: str) -> str:
    """Guess a default timezone from the language code."""
    guesses = {
        "zh_CN": "Asia/Shanghai",
        "zh_HK": "Asia/Hong_Kong",
        "zh_TW": "Asia/Taipei",
        "ja": "Asia/Tokyo",
        "ko": "Asia/Seoul",
        "en_GB": "Europe/London",
        "de": "Europe/Berlin",
        "fr": "Europe/Paris",
        "it": "Europe/Rome",
        "es": "Europe/Madrid",
        "pt": "Europe/Lisbon",
        "pt_BR": "America/Sao_Paulo",
        "ru": "Europe/Moscow",
        "en": "America/New_York",
    }
    return guesses.get(code, "America/New_York")


# ── page 6: Summary ──────────────────────────────────────────────────────

def build_summary_page(shared, nav_view):
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title=_("summary.title", lang))
    page.set_tag("summary")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("summary.title", lang))
    content.append(_page_subtitle("summary.subtitle", lang))

    # Build summary text
    lang_name = "English"
    for l in LANGUAGES:
        if l.code == shared.get("lang"):
            lang_name = f"{l.english_name} ({l.native_name})"
            break

    lines = [
        f"<b>{_('summary.lang', lang)}:</b> {lang_name}",
        f"<b>{_('summary.keyboard', lang)}:</b> {shared.get('keyboard', 'us')}",
        f"<b>{_('summary.disk', lang)}:</b> {shared.get('disk', '?')} "
        f"({shared.get('disk_size', '?')} — {shared.get('disk_model', '?')})",
        f"<b>{_('summary.user', lang)}:</b> {shared.get('full_name', '?')} "
        f"({shared.get('username', '?')})",
        f"<b>{_('summary.hostname', lang)}:</b> {shared.get('hostname', '?')}",
        f"<b>{_('summary.timezone', lang)}:</b> {shared.get('timezone', '?')}",
    ]

    summary_label = Gtk.Label(
        margin_start=48, margin_end=48, margin_top=24, vexpand=True,
    )
    summary_label.set_markup("\n\n".join(lines))
    content.append(summary_label)

    # Warning
    warn = Gtk.Label(label=_("summary.warning", lang))
    warn.add_css_class("warning")
    warn.set_halign(Gtk.Align.CENTER)
    warn.set_margin_top(24)
    content.append(warn)

    def on_install():
        nav_view.push(build_progress_page(shared, nav_view))

    def on_back():
        nav_view.pop()

    content.append(_nav_box(lang, on_back=on_back, on_next=on_install,
                            next_label="nav.install",
                            next_destructive=True))
    page.set_child(content)
    return page


# ── page 7: Progress / Installation ──────────────────────────────────────

def build_progress_page(shared, nav_view):
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title=_("progress.title", lang))
    page.set_tag("progress")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    content.append(_page_title("progress.title", lang))
    content.append(_page_subtitle("progress.subtitle", lang))

    # Progress bar
    progress = Gtk.ProgressBar(margin_start=48, margin_end=48, margin_top=24)
    progress.set_show_text(True)
    content.append(progress)

    # Log view
    log_buf = Gtk.TextBuffer()
    log_view = Gtk.TextView(buffer=log_buf, editable=False, monospace=True,
                            margin_start=48, margin_end=48, margin_top=12,
                            vexpand=True)
    log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    log_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True)
    log_scroll.set_child(log_view)
    content.append(log_scroll)

    # Result widgets (hidden until done)
    result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         spacing=12, margin_top=24, halign=Gtk.Align.CENTER,
                         visible=False)
    result_icon = Gtk.Image()
    result_label = Gtk.Label()
    result_label.add_css_class("title-2")
    result_sub = Gtk.Label()
    result_sub.add_css_class("dim-label")
    result_box.append(result_icon)
    result_box.append(result_label)
    result_box.append(result_sub)
    content.append(result_box)

    reboot_btn = _nav_btn("nav.reboot", lang, lambda: _do_reboot(),
                          css_classes=["suggested-action"])
    reboot_btn.set_visible(False)
    reboot_btn.set_margin_bottom(24)
    reboot_btn.set_halign(Gtk.Align.CENTER)
    content.append(reboot_btn)

    error_btn = _nav_btn("nav.save_log", lang, lambda: _save_log(log_buf))
    error_btn.set_visible(False)
    error_btn.set_margin_bottom(24)
    error_btn.set_halign(Gtk.Align.CENTER)
    content.append(error_btn)

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
            progress.set_visible(False)
            result_box.set_visible(True)
            if success:
                result_icon.set_from_icon_name("emblem-ok-symbolic")
                result_label.set_label(_("done.title", lang))
                result_sub.set_label(_("done.subtitle", lang))
                reboot_btn.set_visible(True)
            else:
                result_icon.set_from_icon_name("dialog-error-symbolic")
                result_label.set_label(_("done.error_title", lang))
                result_sub.set_label(_("done.error_subtitle", lang))
                log(f"ERROR: {error}")
                error_btn.set_visible(True)
            return False
        GLib.idle_add(_done)

    # Run installer in background thread
    installer = Installer(dict(shared), log)
    thread = threading.Thread(target=installer.run, args=(on_done,),
                              daemon=True)
    thread.start()

    # Pulse bar while waiting
    def _pulse():
        if thread.is_alive():
            progress.pulse()
            return True
        return False
    GLib.timeout_add(150, _pulse)

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
    """Save the install log to the live user's home directory."""
    import subprocess
    try:
        text = log_buf.get_text(
            log_buf.get_start_iter(), log_buf.get_end_iter(), False)
        dest = "/home/live/anduinos-install.log"
        with open(dest, "w") as f:
            f.write(text)
        subprocess.run(["chown", "live:live", dest], timeout=5)
    except Exception:
        pass


# ── page 8: Done (standalone, for future use) ────────────────────────────

def build_done_page(shared, nav_view):
    """Simple post-install page. Currently unused — progress page handles both states."""
    lang = shared.get("lang", "en")
    page = Adw.NavigationPage(title=_("done.title", lang))
    page.set_tag("done")
    page.set_child(Gtk.Label(label=_("done.title", lang)))
    return page


# ── build all pages (called from main.py) ─────────────────────────────────

def build_all_pages(shared: dict, nav_view: Adw.NavigationView):
    """Return a list of all pages. The first page is the entry point."""
    return [
        build_welcome_page(shared, nav_view),
        # Other pages are pushed on demand — we only pre-build the first one.
    ]
