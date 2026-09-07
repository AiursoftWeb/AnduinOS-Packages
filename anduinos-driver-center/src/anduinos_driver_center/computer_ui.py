"""A shareable computer overview with optional, read-only memory authorization."""
from __future__ import annotations

import gettext
import json
import math
from pathlib import Path
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from .computer import Computer, capacity, scan_computer

_ = gettext.gettext
MEMORY_HELPER = '/usr/libexec/anduinos-driver-center/memory-helper'


class ComputerPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.info = None
        self.expanded = False
        self.memory_attempted = False
        self.memory_modules = []
        self.memory_message = ''
        self.memory_pending = False
        self._scan_generation = 0
        css = Gtk.CssProvider()
        css.load_from_data(b'''
            .computer-sheet { padding: 26px; border-radius: 20px; }
            .computer-hero { padding: 4px 0 20px; }
            .computer-brand { font-size: 28px; font-weight: 800; letter-spacing: -1px; }
            .computer-value { font-size: 15px; font-weight: 600; }
            .computer-spec { padding: 11px 0; border-top: 1px solid alpha(@window_fg_color, .08); }
            .computer-symbol { color: @accent_color; }
        ''')
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_overlay_scrolling(False)
        clamp = Adw.Clamp(maximum_size=880, tightening_threshold=650)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                            margin_top=22, margin_bottom=22, margin_start=22, margin_end=22)
        clamp.set_child(self.body)
        scroll.set_child(clamp)
        self.append(scroll)
        self.reload()

    def reload(self):
        self._scan_generation += 1
        generation = self._scan_generation
        if self.info is None:
            self.body.append(Gtk.Spinner(spinning=True, height_request=48))
        def worker():
            try:
                info = scan_computer()
            except Exception:
                # A failed probe must not leave a permanently spinning page.
                info = Computer()
            GLib.idle_add(self._loaded, generation, info)
        threading.Thread(target=worker, daemon=True).start()

    def _loaded(self, generation, info):
        if generation == self._scan_generation:
            self.info = info
            self.render()
        return GLib.SOURCE_REMOVE

    def _label(self, text, style=None):
        label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True, hexpand=True)
        if style:
            label.add_css_class(style)
        return label

    def _row(self, box, icon, title, value, detail=''):
        row = Gtk.Box(spacing=16)
        row.add_css_class('computer-spec')
        if icon in {'processor-symbolic', 'memory-symbolic', 'graphics-card-symbolic', 'motherboard-symbolic'}:
            path = Path('/usr/share/anduinos-driver-center/illustrations') / (icon + '.svg')
            if not path.is_file():
                path = Path(__file__).resolve().parents[2] / 'resources' / (icon + '.svg')
            image = Gtk.Image.new_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(path))))
        else:
            image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(22)
        image.add_css_class('computer-symbol')
        image.set_valign(Gtk.Align.CENTER)
        row.append(image)
        label = self._label(title, 'dim-label')
        label.set_hexpand(False)
        label.set_size_request(105, -1)
        row.append(label)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        content.append(self._label(value or _('Not available'), 'computer-value'))
        if detail:
            content.append(self._label(detail, 'dim-label'))
        row.append(content)
        box.append(row)
        return row

    def _displays(self):
        result = []
        monitors = self.get_display().get_monitors()
        for index in range(monitors.get_n_items()):
            monitor = monitors.get_item(index)
            connector = monitor.get_connector() or ''
            edid = self.info.displays.get(connector, {})
            name = edid.get('model') or monitor.get_model() or connector or _('Display')
            size = edid.get('size', '')
            if not size and monitor.get_width_mm() and monitor.get_height_mm():
                size = f'{(monitor.get_width_mm()**2 + monitor.get_height_mm()**2)**.5 / 25.4:.1f}″'
            geometry = monitor.get_geometry()
            scale = monitor.get_scale() if hasattr(monitor, 'get_scale') else monitor.get_scale_factor()
            dimensions = f'{round(geometry.width * scale)} × {round(geometry.height * scale)}'
            rate = monitor.get_refresh_rate() / 1000
            mode = ' · '.join(filter(None, [size, dimensions, f'{round(rate, 2):g} Hz' if rate else '']))
            connection = ''
            if connector.startswith(('eDP-', 'LVDS-', 'DSI-')):
                connection = _('Built-in display')
            elif connector.startswith(('DP-', 'HDMI-', 'DVI-', 'VGA-', 'DisplayPort-')):
                connection = _('External display')
            details = ' · '.join(filter(None, [connection, connector, _('Current mode'), f'{scale:g}×']))
            result.append((name, mode, details))
        return result

    def render(self):
        child = self.body.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.body.remove(child)
            child = following
        info = self.info
        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sheet.add_css_class('card')
        sheet.add_css_class('computer-sheet')
        hero = Gtk.Box(spacing=20)
        hero.add_css_class('computer-hero')
        if info.anduinos:
            path = Path('/usr/share/anduinos-driver-center/illustrations/anduinos-logo.svg')
            if not path.is_file():
                path = Path(__file__).resolve().parents[2] / 'resources/anduinos-logo.svg'
            picture = Gtk.Picture.new_for_filename(str(path))
            picture.set_size_request(80, 80)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            hero.append(picture)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        heading.append(self._label(info.system or _('About This Computer'), 'computer-brand'))
        if info.model:
            heading.append(self._label(info.model, 'title-3'))
        heading.append(self._label(_('Hardware overview'), 'dim-label'))
        hero.append(heading)
        sheet.append(hero)
        cpu = info.cpu
        topology = []
        try:
            cores = int(cpu['Core(s) per socket']) * int(cpu['Socket(s)'])
            topology.append(_('%s cores') % cores)
        except (KeyError, ValueError):
            pass
        if cpu.get('CPU(s)'):
            topology.append(_('%s threads') % cpu['CPU(s)'])
        self._row(sheet, 'processor-symbolic', _('Processor'), cpu.get('Model name', '').replace('(R)', '').replace('(TM)', ''), ' · '.join(topology))
        self._row(sheet, 'memory-symbolic', _('Memory'), capacity(info.memory, True) if info.memory else '', _('Usable by the system'))
        for gpu in info.pci.get('graphics', []) or [{}]:
            self._row(sheet, 'graphics-card-symbolic', _('Graphics'), gpu.get('model', '').split('[')[-1].rstrip(']') if '[' in gpu.get('model', '') else gpu.get('model', ''), gpu.get('memory', ''))
        system_disks = [disk for disk in info.disks if disk.system]
        for disk in system_disks:
            self._row(sheet, 'drive-harddisk-symbolic', _('System disk'), disk.model,
                      ' · '.join(filter(None, [capacity(disk.size), disk.transport.upper(), disk.medium])))
        if not system_disks:
            self._row(sheet, 'drive-harddisk-symbolic', _('System disk'), _('Not available'))
        for name, mode, _connector in self._displays():
            self._row(sheet, 'video-display-symbolic', _('Display'), name, mode)
        self._row(sheet, 'motherboard-symbolic', _('Motherboard'), info.board)
        self.body.append(sheet)

        if self.expanded:
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            details.add_css_class('card')
            details.add_css_class('computer-sheet')
            details.append(self._label(_('Detailed specifications'), 'title-2'))
            self._row(details, 'application-x-firmware-symbolic', _('BIOS / UEFI'), info.bios)
            cpu_details = ['%s: %s' % (key, cpu[key]) for key in
                           ('Architecture', 'L1d cache', 'L1i cache', 'L2 cache', 'L3 cache', 'Virtualization') if cpu.get(key)]
            try:
                frequency = float(cpu.get('CPU max MHz', '')) / 1000
                if math.isfinite(frequency) and frequency > 0:
                    cpu_details.insert(0, _('Maximum frequency: %s') % f'{frequency:.2f} GHz')
            except ValueError:
                pass
            self._row(details, 'processor-symbolic', _('Processor'), cpu.get('Model name', ''), '\n'.join(cpu_details))
            for disk in info.disks:
                self._row(details, 'drive-harddisk-symbolic', _('Storage'), disk.model,
                          ' · '.join(filter(None, [capacity(disk.size), disk.transport.upper(), disk.medium])) + '\n' + '\n'.join(disk.volumes))
            for group, title, icon in [('graphics', _('Graphics'), 'graphics-card-symbolic'),
                                        ('network', _('Network'), 'network-wired-symbolic'),
                                        ('audio', _('Audio'), 'audio-card-symbolic')]:
                for device in info.pci.get(group, []):
                    self._row(details, icon, title, device['model'], ' · '.join(filter(None, [device.get('driver'), device.get('version'), device.get('memory'), device.get('slot')])))
            for name, mode, connector in self._displays():
                self._row(details, 'video-display-symbolic', _('Display'), name, mode + '\n' + connector)
            for module in self.memory_modules:
                self._row(details, 'memory-symbolic', _('Memory module'),
                          ' · '.join(filter(None, [module.get('Size'), module.get('Type'), module.get('Manufacturer')])),
                          '\n'.join('%s: %s' % (key, value) for key, value in module.items() if key not in {'Size', 'Type', 'Manufacturer'}))
            self.body.append(details)
            memory_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            if not self.memory_attempted:
                memory_box.append(self._label(_('Memory type and speed require optional administrator authentication.'), 'dim-label'))
                button = Gtk.Button(label=_('Read memory specifications'), halign=Gtk.Align.CENTER)
                button.connect('clicked', self._read_memory)
                memory_box.append(button)
            elif self.memory_pending:
                memory_box.append(Gtk.Spinner(spinning=True))
            elif self.memory_message:
                memory_box.append(self._label(self.memory_message, 'dim-label'))
            self.body.append(memory_box)
            self._system_sections()
        toggle = Gtk.Button(label=_('Hide details') if self.expanded else _('Show detailed specifications'), halign=Gtk.Align.CENTER)
        toggle.add_css_class('pill')
        toggle.connect('clicked', self._toggle)
        self.body.append(toggle)

    def _section(self, title):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        section.add_css_class('card')
        section.add_css_class('computer-sheet')
        section.append(self._label(title, 'title-2'))
        self.body.append(section)
        return section

    def _usage_row(self, section, icon, title, used, total, detail=''):
        value = _('Not available')
        if used is not None and total is not None:
            value = _('%(used)s used / %(total)s') % {
                'used': capacity(used, True), 'total': capacity(total, True)}
        self._row(section, icon, title, value, detail)
        if used is not None and total:
            bar = Gtk.ProgressBar(fraction=min(1, max(0, used / total)))
            bar.set_margin_bottom(8)
            section.append(bar)

    def _system_sections(self):
        info = self.info
        state = info.details
        system = self._section(_('System and desktop'))
        self._row(system, 'computer-symbolic', _('System'), info.system, info.kernel)
        self._row(system, 'preferences-desktop-display-symbolic', _('Desktop'), state.desktop or info.desktop)
        self._row(system, 'preferences-desktop-display-symbolic', _('Window manager'), state.window_manager,
                  {'wayland': 'Wayland', 'x11': 'X11'}.get(state.session_type, state.session_type))
        self._row(system, 'package-x-generic-symbolic', 'dpkg',
                  f'{state.dpkg_count:,}' if state.dpkg_count is not None else '', _('Installed packages'))
        flatpak_row = self._row(system, 'package-x-generic-symbolic', 'Flatpak',
                                 f'{state.flatpak_count:,}' if state.flatpak_count is not None else '',
                                 _('Applications and runtimes'))
        flatpak_row.set_tooltip_text(_('Applications and runtimes across user and system installations; locale and debug extensions excluded.'))
        appearance = state.appearance
        for key, title in [('gtk-theme', _('GTK theme')), ('shell-theme', _('Shell theme')),
                           ('icon-theme', _('Icon theme')), ('font-name', _('Font'))]:
            if appearance.get(key):
                self._row(system, 'applications-graphics-symbolic', title, appearance[key])
        if appearance.get('cursor-theme'):
            size = appearance.get('cursor-size', '')
            self._row(system, 'input-mouse-symbolic', _('Cursor'), appearance['cursor-theme'],
                      f'{size} px' if size else '')

        usage = self._section(_('Current usage'))
        usage.append(self._label(_('Values from the latest scan. Use Scan again to refresh.'), 'dim-label'))
        uptime = ''
        if state.uptime is not None:
            days, remaining = divmod(state.uptime, 86400)
            hours, remaining = divmod(remaining, 3600)
            pattern = (_('%(days)d d %(hours)d h %(minutes)d min') if days
                       else _('%(hours)d h %(minutes)d min'))
            uptime = pattern % {'days': days, 'hours': hours, 'minutes': remaining // 60}
        self._row(usage, 'preferences-system-time-symbolic', _('Uptime'), uptime)
        self._usage_row(usage, 'memory-symbolic', _('Memory'), state.memory_used, state.memory_total)
        if state.swap_total == 0:
            self._row(usage, 'drive-harddisk-symbolic', 'Swap', _('Not configured'))
        else:
            self._usage_row(usage, 'drive-harddisk-symbolic', 'Swap', state.swap_used, state.swap_total)
        for filesystem in state.filesystems:
            description = ' · '.join([filesystem.source, filesystem.filesystem,
                                     _('Available: %s') % capacity(filesystem.available, True)])
            self._usage_row(usage, 'drive-harddisk-symbolic', filesystem.target,
                            filesystem.used, filesystem.total, description)

    def _toggle(self, _button):
        self.expanded = not self.expanded
        self.render()

    def _read_memory(self, _button):
        if self.memory_attempted:
            return
        self.memory_attempted = True
        self.memory_pending = True
        self.render()
        try:
            process = Gio.Subprocess.new(['pkexec', MEMORY_HELPER], Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            process.communicate_utf8_async(None, None, self._memory_done)
        except GLib.Error:
            self._memory_result(None)

    def _memory_done(self, process, result):
        modules = None
        try:
            _ok, stdout, _stderr = process.communicate_utf8_finish(result)
            if process.get_successful():
                data = json.loads(stdout)
                if isinstance(data, list) and all(isinstance(module, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in module.items()) for module in data):
                    modules = data
        except (GLib.Error, ValueError):
            pass
        self._memory_result(modules)

    def _memory_result(self, modules):
        self.memory_pending = False
        self.memory_modules = modules or []
        self.memory_message = '' if modules else _('Memory specifications are unavailable. Other hardware details remain available.')
        self.render()
