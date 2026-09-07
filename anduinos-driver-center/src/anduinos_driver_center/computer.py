"""Unprivileged, best-effort hardware inventory. No authentication or mutations."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess


def read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def command_result(*args: str) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=12,
                                env={**os.environ, "LC_ALL": "C"}, check=False)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def command(*args: str) -> str:
    return command_result(*args) or ""


def json_object(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def release_fields(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Z_]+", key):
            try:
                parts = shlex.split(value, comments=True)
                if parts:
                    result[key] = " ".join(parts)
            except ValueError:
                pass
    return result


def distribution(etc: Path = Path('/etc')) -> tuple[str, bool]:
    release = release_fields(read(etc / 'os-release'))
    if release.get('ID') or release.get('NAME'):
        branded = release.get('ID', '').lower() == 'anduinos'
        if not release.get('ID'):
            branded = release.get('NAME', '').lower() == 'anduinos'
        return release.get('PRETTY_NAME') or release.get('NAME') or release['ID'], branded
    legacy = release_fields(read(etc / 'lsb-release'))
    return (legacy.get('DISTRIB_DESCRIPTION') or legacy.get('DISTRIB_ID') or 'Linux',
            legacy.get('DISTRIB_ID', '').lower() == 'anduinos')


def clean(value: str) -> str:
    value = value.strip()
    return '' if value.lower() in {'', 'none', 'unknown', 'default string', 'not specified',
        'to be filled by o.e.m.', 'system product name', 'system manufacturer'} else value


def capacity(size: int, binary: bool = False) -> str:
    base = 1024 if binary else 1000
    units = ('B', 'KiB', 'MiB', 'GiB', 'TiB') if binary else ('B', 'kB', 'MB', 'GB', 'TB')
    number = float(size)
    for unit in units:
        if number < base or unit == units[-1]:
            return f'{number:,.2f}'.rstrip('0').rstrip('.') + ' ' + unit
        number /= base
    return ''


@dataclass
class Disk:
    name: str
    model: str
    size: int
    transport: str
    rotational: bool
    system: bool
    volumes: list[str] = field(default_factory=list)

    @property
    def medium(self) -> str:
        # Non-rotational media can also be eMMC or USB flash, not necessarily SSDs.
        if self.rotational:
            return 'HDD'
        return 'SSD' if self.transport == 'nvme' or 'SSD' in self.model.upper() else ''


def disks_from_json(data: dict) -> list[Disk]:
    disks: dict[str, Disk] = {}
    def descendants(node):
        yield node
        for child in node.get('children', []):
            yield from descendants(child)
    for node in descendants({'children': data.get('blockdevices', [])}):
        name = node.get('name', '')
        if node.get('type') != 'disk' or name.startswith(('zram', 'ram', 'loop')):
            continue
        children = list(descendants(node))
        system = any('/' in (child.get('mountpoints') or []) for child in children)
        volumes = []
        for child in children:
            # Do not include home-directory names in a shareable inventory.
            mounts = child.get('mountpoints') or []
            parts = [child.get('path', ''), capacity(int(child.get('size') or 0)), child.get('fstype') or '']
            if '/' in mounts:
                parts.append('/')
            volumes.append(' · '.join(p for p in parts if p))
        disks[name] = Disk(name, clean(node.get('model') or '') or name,
                           int(node.get('size') or 0), node.get('tran') or '',
                           bool(node.get('rota')), system, volumes)
    return list(disks.values())


def pci_devices(text: str) -> dict[str, list[dict[str, str]]]:
    result = {'graphics': [], 'network': [], 'audio': []}
    for block in text.strip().split('\n\n'):
        fields = dict(line.split(':\t', 1) for line in block.splitlines() if ':\t' in line)
        kind = fields.get('Class', '').lower()
        group = ('graphics' if any(word in kind for word in ('vga', '3d controller', 'display controller'))
                 else 'network' if any(word in kind for word in ('network', 'ethernet'))
                 else 'audio' if any(word in kind for word in ('audio', 'multimedia')) else None)
        if group:
            result[group].append({'model': ' '.join(filter(None, [fields.get('Vendor'), fields.get('Device')])),
                                  'driver': fields.get('Driver', ''), 'slot': fields.get('Slot', '')})
    return result


def edid_info(data: bytes) -> dict[str, str]:
    if len(data) < 128 or data[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00' or sum(data[:128]) % 256:
        return {}
    name = ''
    for offset in (54, 72, 90, 108):
        block = data[offset:offset + 18]
        if block[:5] == b'\x00\x00\x00\xfc\x00':
            name = block[5:18].decode('ascii', errors='replace').strip('\n\x00 ')
    code = int.from_bytes(data[8:10], 'big')
    vendor = ''.join(chr(64 + ((code >> shift) & 31)) for shift in (10, 5, 0))
    size = f'{math.hypot(data[21], data[22]) / 2.54:.1f}″' if data[21] and data[22] else ''
    return {'model': name or vendor, 'size': size}


@dataclass
class FilesystemUsage:
    source: str
    target: str
    filesystem: str
    total: int
    used: int
    available: int


@dataclass
class SystemDetails:
    desktop: str = ''
    window_manager: str = ''
    session_type: str = ''
    appearance: dict[str, str] = field(default_factory=dict)
    dpkg_count: int | None = None
    flatpak_count: int | None = None
    memory_total: int | None = None
    memory_used: int | None = None
    swap_total: int | None = None
    swap_used: int | None = None
    uptime: int | None = None
    filesystems: list[FilesystemUsage] = field(default_factory=list)


def memory_usage(text: str) -> dict[str, int | None]:
    values = {key: int(value) * 1024 for key, value in
              re.findall(r'^(\w+):\s+(\d+) kB$', text, re.M)}
    total, available = values.get('MemTotal'), values.get('MemAvailable')
    swap, free = values.get('SwapTotal'), values.get('SwapFree')
    return {
        'memory_total': total,
        'memory_used': max(0, total - available) if total is not None and available is not None else None,
        'swap_total': swap,
        'swap_used': max(0, swap - free) if swap is not None and free is not None else None,
    }


def package_counts() -> tuple[int | None, int | None]:
    dpkg = command_result('dpkg-query', '-W', '-f=${db:Status-Status}\n')
    flatpak = command_result('flatpak', 'list', '--columns=ref,installation')
    # A ref in two installations occupies two installed entries. The default
    # flatpak listing omits auxiliary locale/debug extensions, like fastfetch.
    return (sum(line.strip() == 'installed' for line in dpkg.splitlines()) if dpkg is not None else None,
            len({line.strip() for line in flatpak.splitlines() if line.strip()}) if flatpak is not None else None)


def appearance_settings() -> dict[str, str]:
    # Schema presence varies between desktop environments. Read known keys only;
    # no Settings object is created for a missing schema (which would abort GLib).
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return {}
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return {}
        result = {}
        for schema_name, keys in (
            ('org.gnome.desktop.interface', ('gtk-theme', 'icon-theme', 'font-name', 'cursor-theme', 'cursor-size')),
            ('org.gnome.shell.extensions.user-theme', ('name',)),
        ):
            schema = source.lookup(schema_name, True)
            if schema is None:
                continue
            settings = Gio.Settings.new_full(schema, None, None)
            for key in keys:
                if schema.has_key(key):
                    value = settings.get_value(key).unpack()
                    if value:
                        result['shell-theme' if key == 'name' else key] = str(value)
        return result
    except (GLib.Error, TypeError, RuntimeError):
        return {}


def filesystem_usage(data: dict, statvfs=None) -> list[FilesystemUsage]:
    statvfs = statvfs or os.statvfs
    groups = {}
    for mount in data.get('filesystems', []):
        source = (mount.get('source') or '').split('[', 1)[0]
        target = mount.get('target') or ''
        # Restrict statvfs to local block-backed storage: do not contact network
        # mounts or include tmpfs, containers, or read-only snap loop images.
        if not source.startswith('/dev/') or source.startswith('/dev/loop') or not target:
            continue
        key = (mount.get('maj:min') or source, mount.get('fstype'))
        groups.setdefault(key, []).append((source, target, mount.get('fstype') or ''))
    result = []
    for mounts in groups.values():
        # Prefer /, then the shortest accessible mount of a shared filesystem.
        for source, target, filesystem in sorted(mounts, key=lambda entry: (entry[1] != '/', len(entry[1]), entry[1])):
            try:
                stat = statvfs(target)
            except OSError:
                continue
            if stat.f_blocks <= 0:
                continue
            display_target = re.sub(r'^(/(?:home|media|run/media)/)[^/]+', r'\1…', target)
            result.append(FilesystemUsage(source, display_target, filesystem,
                stat.f_blocks * stat.f_frsize,
                max(0, stat.f_blocks - stat.f_bfree) * stat.f_frsize,
                max(0, stat.f_bavail) * stat.f_frsize))
            break
    return sorted(result, key=lambda item: (item.target != '/', item.target))


def scan_details() -> SystemDetails:
    info = SystemDetails(**memory_usage(read(Path('/proc/meminfo'))))
    try:
        info.uptime = max(0, int(float(read(Path('/proc/uptime')).split()[0])))
    except (ValueError, IndexError, OverflowError):
        pass
    info.desktop = os.environ.get('XDG_CURRENT_DESKTOP', '')
    info.session_type = os.environ.get('XDG_SESSION_TYPE', '')
    if 'GNOME' in info.desktop.upper().split(':'):
        # Ask the running shell, not a binary whose installed version may differ.
        version = command('gdbus', 'call', '--session', '--dest', 'org.gnome.Shell',
                          '--object-path', '/org/gnome/Shell', '--method',
                          'org.freedesktop.DBus.Properties.Get', 'org.gnome.Shell', 'ShellVersion')
        match = re.search(r"'([0-9][0-9A-Za-z.+~-]*)'", version)
        if match:
            info.desktop = 'GNOME ' + match[1]
            info.window_manager = 'Mutter'
        info.appearance = appearance_settings()
    info.dpkg_count, info.flatpak_count = package_counts()
    info.filesystems = filesystem_usage(json_object(command('findmnt', '--json', '--list', '--real',
                                                           '--output', 'SOURCE,TARGET,FSTYPE,MAJ:MIN')))
    return info


@dataclass
class Computer:
    system: str = ''
    anduinos: bool = False
    model: str = ''
    cpu: dict[str, str] = field(default_factory=dict)
    memory: int = 0
    board: str = ''
    bios: str = ''
    kernel: str = ''
    desktop: str = ''
    disks: list[Disk] = field(default_factory=list)
    pci: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    displays: dict[str, dict[str, str]] = field(default_factory=dict)
    details: SystemDetails = field(default_factory=SystemDetails)


def scan_computer() -> Computer:
    info = Computer()
    info.system, info.anduinos = distribution()
    dmi = Path('/sys/class/dmi/id')
    info.model = ' '.join(filter(None, (clean(read(dmi / 'sys_vendor')), clean(read(dmi / 'product_name')))))
    info.board = ' '.join(filter(None, (clean(read(dmi / 'board_vendor')), clean(read(dmi / 'board_name')))))
    info.bios = ' · '.join(filter(None, (clean(read(dmi / 'bios_vendor')), clean(read(dmi / 'bios_version')), read(dmi / 'bios_date'))))
    cpu_data = json_object(command('lscpu', '--json'))
    def cpu_fields(rows):
        for row in rows:
            yield row['field'].rstrip(':'), str(row.get('data') or '')
            yield from cpu_fields(row.get('children', []))
    info.cpu = dict(cpu_fields(cpu_data.get('lscpu', [])))
    match = re.search(r'^MemTotal:\s+(\d+)', read(Path('/proc/meminfo')), re.M)
    info.memory = int(match[1]) * 1024 if match else 0
    info.kernel = f'{os.uname().release} · {os.uname().machine}'
    info.desktop = ' · '.join(filter(None, [os.environ.get('XDG_CURRENT_DESKTOP'), os.environ.get('XDG_SESSION_TYPE')]))
    info.disks = disks_from_json(json_object(command('lsblk', '--json', '--bytes', '--tree', '--output',
        'NAME,PATH,TYPE,SIZE,MODEL,TRAN,ROTA,MOUNTPOINTS,FSTYPE')))
    info.pci = pci_devices(command('lspci', '-vmmk'))
    # VRAM is not reliably available through the generic PCI API.
    nvidia = command('nvidia-smi', '--query-gpu=pci.bus_id,memory.total,driver_version', '--format=csv,noheader,nounits') if any(d['driver'] == 'nvidia' for d in info.pci['graphics']) else ''
    for row in csv.reader(nvidia.splitlines()):
        if len(row) == 3:
            bus, memory, version = (value.strip() for value in row)
            for device in info.pci['graphics']:
                if bus.lower()[-7:] == device['slot'].lower()[-7:]:
                    if memory.isdigit():
                        device['memory'] = capacity(int(memory) * 1024**2, True)
                    device['version'] = version
    for connector in Path('/sys/class/drm').glob('card*-*'):
        if read(connector / 'status') != 'connected':
            continue
        try:
            data = edid_info((connector / 'edid').read_bytes())
        except OSError:
            data = {}
        info.displays[connector.name.split('-', 1)[1]] = data
    info.details = scan_details()
    return info
