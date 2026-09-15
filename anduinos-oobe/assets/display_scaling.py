"""Primary-monitor scaling policy and Mutter transport, without GTK widgets.

146 effective PPI for likely laptop panels; 96 for other physical displays.
These are comfort heuristics, not a claim to reproduce Windows defaults.
"""

import math
from pathlib import Path
import re

from gi.repository import Gio, GLib

BUS_NAME = 'org.gnome.Mutter.DisplayConfig'
OBJECT_PATH = '/org/gnome/Mutter/DisplayConfig'
EPSILON = 1e-5


def same_scale(a, b):
    return a is not None and b is not None and abs(a - b) < EPSILON


def format_scale(scale):
    return f'{scale * 100:.2f}'.rstrip('0').rstrip('.') + '%'


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def edid_size(data):
    """Return millimetres, preferring the first detailed timing over rounded cm."""
    if (len(data) < 128 or data[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00'
            or sum(data[:128]) % 256):
        return None
    size = (data[21] * 10, data[22] * 10)
    for offset in range(54, 126, 18):
        d = data[offset:offset + 18]
        if d[:2] == b'\0\0':
            continue
        width = d[12] + ((d[14] >> 4) << 8)
        height = d[13] + ((d[14] & 15) << 8)
        if width and height:
            size = (width, height)
        break
    return size if valid_size(size) else None


def valid_size(size):
    return size and all(math.isfinite(v) and 30 <= v <= 10000 for v in size)


def physical_size(spec, props, drm_root=Path('/sys/class/drm')):
    connector = spec[0]
    if connector.lower().startswith(('virtual', 'vnc', 'rdp', 'remote')):
        return None
    matches = [p for p in drm_root.glob('card*-*')
               if re.sub(r'^card\d+-', '', p.name) == connector
               and read_text(p / 'status') == 'connected']
    if len(matches) == 1:
        try:
            size = edid_size((matches[0] / 'edid').read_bytes())
            if size:
                return size
        except OSError:
            pass
    # Newer Mutter versions expose dimensions directly and disambiguate GPUs.
    size = (props.get('width-mm', 0), props.get('height-mm', 0))
    return size if valid_size(size) else None


def hardware_evidence():
    chassis = read_text('/sys/class/dmi/id/chassis_type')
    battery = any(read_text(p / 'type') == 'Battery'
                  and read_text(p / 'scope') != 'Device'
                  for p in Path('/sys/class/power_supply').glob('*'))
    return chassis, battery


def laptop_score(builtin, size, chassis, battery):
    # External screens never inherit the laptop identity of the host.
    if not builtin:
        return 0
    return (4 + (4 if chassis in {'8', '9', '10', '14', '30', '31', '32'} else 0)
            + (2 if battery else 0)
            + (1 if size and math.hypot(*size) / 25.4 < 20 else 0))


def current_mode(monitor):
    return next(m for m in monitor[1] if m[6].get('is-current'))


def primary_info(state, size_reader=physical_size, evidence=None):
    _, monitors, logical, props = state
    primary = next(l for l in logical if l[4])
    by_spec = {tuple(m[0]): m for m in monitors}
    group = [by_spec[tuple(s)] for s in primary[5]]
    modes = [current_mode(m) for m in group]
    supported = sorted({s for s in modes[0][5] if math.isfinite(s) and s > 0
                        and all(any(same_scale(s, t) for t in m[5]) for m in modes)})
    # Per-monitor changes cannot satisfy a global-only backend with several screens.
    if props.get('global-scale-required') and len(logical) > 1:
        supported = [s for s in supported if same_scale(s, primary[2])]
    spec, _, monitor_props = group[0]
    size = size_reader(spec, monitor_props) if len(group) == 1 else None
    builtin = monitor_props.get('is-builtin', spec[0].startswith(('eDP-', 'LVDS-', 'DSI-')))
    chassis, battery = evidence if evidence is not None else hardware_evidence()
    score = laptop_score(builtin, size, chassis, battery)
    target = 146 if score >= 6 else 96
    ppi = math.hypot(modes[0][1], modes[0][2]) * 25.4 / math.hypot(*size) if size else None
    recommended = min(supported, key=lambda s: abs(ppi / s - target)) if ppi and supported else None
    return dict(key=tuple(tuple(s) for s in primary[5]),
                name=monitor_props.get('display-name', spec[0]),
                scale=primary[2], supported=supported, recommended=recommended,
                ppi=ppi, effective_ppi=ppi / primary[2] if ppi else None,
                target_ppi=target, laptop_score=score)


def monitor_properties(props):
    result = {}
    for source, target, signature in (
        ('is-underscanning', 'underscanning', 'b'),
        ('color-mode', 'color-mode', 'u'),
        ('rgb-range', 'rgb-range', 'u'),
    ):
        if source in props:
            result[target] = GLib.Variant(signature, props[source])
    return result


def build_config(state, scale, expected_key):
    """Preserve modes/rotation/colour and reattach neighbouring logical screens.

    A spanning tree of existing shared edges keeps ordinary multi-screen layouts
    connected. Mutter validates the resulting geometry before any mutation.
    """
    serial, monitors, logical, props = state
    primary = next(i for i, l in enumerate(logical) if l[4])
    if tuple(tuple(s) for s in logical[primary][5]) != expected_key:
        raise ValueError('Primary monitor changed')
    if props.get('global-scale-required') and len(logical) > 1 and not same_scale(scale, logical[primary][2]):
        raise ValueError('This backend requires a global scale')
    by_spec = {tuple(m[0]): m for m in monitors}
    configs, old_sizes, new_sizes = [], [], []
    for i, (x, y, old_scale, transform, is_primary, specs, _) in enumerate(logical):
        new_scale = scale if i == primary else old_scale
        items = []
        for spec in specs:
            monitor = by_spec[tuple(spec)]
            mode = current_mode(monitor)
            if not any(same_scale(new_scale, s) for s in mode[5]):
                raise ValueError('Scale not supported by the current mode')
            items.append((spec[0], mode[0], monitor_properties(monitor[2])))
        mode = current_mode(by_spec[tuple(specs[0])])
        width, height = mode[1:3]
        if transform % 2:
            width, height = height, width
        physical = props.get('layout-mode', 1) == 2
        old_sizes.append((round(width / (1 if physical else old_scale)), round(height / (1 if physical else old_scale))))
        new_sizes.append((round(width / (1 if physical else new_scale)), round(height / (1 if physical else new_scale))))
        configs.append([x, y, new_scale, transform, is_primary, items])

    placed = {primary}
    queue = [primary]
    for i in queue:
        x, y = logical[i][:2]
        w, h = old_sizes[i]
        nx, ny = configs[i][:2]
        nw, nh = new_sizes[i]
        for j, other in enumerate(logical):
            if j in placed:
                continue
            ox, oy = other[:2]
            ow, oh = old_sizes[j]
            jw, jh = new_sizes[j]
            vertical_overlap = max(y, oy) < min(y + h, oy + oh)
            horizontal_overlap = max(x, ox) < min(x + w, ox + ow)
            # Retain offset along shared edge, clamped so the screens still touch.
            dy = min(max(oy - y, 1 - jh), nh - 1)
            dx = min(max(ox - x, 1 - jw), nw - 1)
            if vertical_overlap and ox == x + w:
                pos = (nx + nw, ny + dy)
            elif vertical_overlap and ox + ow == x:
                pos = (nx - jw, ny + dy)
            elif horizontal_overlap and oy == y + h:
                pos = (nx + dx, ny + nh)
            elif horizontal_overlap and oy + oh == y:
                pos = (nx + dx, ny - jh)
            else:
                continue
            configs[j][:2] = pos
            placed.add(j)
            queue.append(j)
    if len(placed) != len(logical):
        raise ValueError('Display layout is not connected')
    min_x = min(c[0] for c in configs)
    min_y = min(c[1] for c in configs)
    for c in configs:
        c[0] -= min_x
        c[1] -= min_y
    extra = {}
    if props.get('supports-changing-layout-mode') and 'layout-mode' in props:
        extra['layout-mode'] = GLib.Variant('u', props['layout-mode'])
    leased = [tuple(m[0]) for m in monitors if m[2].get('is-for-lease')]
    if leased:
        extra['monitors-for-lease'] = GLib.Variant('a(ssss)', leased)
    return serial, configs, extra


class DisplayClient:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(self, method, params=None):
        return self.bus.call_sync(BUS_NAME, OBJECT_PATH, BUS_NAME, method, params,
                                  None, Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def state(self):
        return self.call('GetCurrentState')

    def apply(self, scale, expected_key):
        state = self.state()  # Fresh serial and topology; never overwrite a stale layout.
        serial, configs, extra = build_config(state, scale, expected_key)
        for method in (0, 2):  # Verify, then persist through Mutter.
            self.call('ApplyMonitorsConfig', GLib.Variant(
                '(uua(iiduba(ssa{sv}))a{sv})', (serial, method, configs, extra)))
        result = primary_info(self.state())
        if result['key'] != expected_key or not same_scale(result['scale'], scale):
            raise ValueError('Display configuration changed while applying')
        return result
