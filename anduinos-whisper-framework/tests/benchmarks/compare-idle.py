#!/usr/bin/env python3
"""Compare idle services under a private bus; no model loading or microphone.

dbus-run-session -- env ANDUINOS_ISOLATED_TEST_BUS=1 python3 tests/benchmarks/compare-idle.py obj/amd64/anduinos-whisper-framework
"""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parents[2]
APP = 'com.anduinos.VoiceTyping'
OBJECT = '/com/anduinos/VoiceTyping'


def main():
    if os.environ.get('ANDUINOS_ISOLATED_TEST_BUS') != '1':
        raise SystemExit('Use dbus-run-session with ANDUINOS_ISOLATED_TEST_BUS=1')
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                  'org.freedesktop.DBus', 'RequestName',
                                  GLib.Variant('(su)', ('org.gnome.Shell', 4)),
                                  None, Gio.DBusCallFlags.NONE, 2000, None)
    assert result.unpack() == (1,), 'Use a private dbus-run-session, never the desktop bus'

    def call(method):
        return connection.call_sync(APP, OBJECT, APP, method, None, None,
                                    Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()

    records = []
    with tempfile.TemporaryDirectory(prefix='voice-idle-') as directory:
        environment = dict(os.environ, PYTHONPATH=str(ROOT / 'tests' / 'reference'),
                           PYTHONDONTWRITEBYTECODE='1', GSETTINGS_BACKEND='memory',
                           GSETTINGS_SCHEMA_DIR=directory, XDG_CACHE_HOME=directory,
                           XDG_RUNTIME_DIR=directory, PIPEWIRE_RUNTIME_DIR=directory,
                           GIO_USE_VFS='local', GTK_A11Y='none', NO_AT_BRIDGE='1')
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'SESSION_MANAGER', 'AT_SPI_BUS_ADDRESS'):
            environment.pop(key, None)
        schema = ROOT / 'data/com.anduinos.voice-typing.gschema.xml'
        Path(directory, schema.name).write_bytes(schema.read_bytes())
        subprocess.run(['glib-compile-schemas', directory], check=True)
        for iteration in range(3):
            for implementation, command in [
                    ('python', [sys.executable, str(ROOT / 'tests/reference/python-service')]),
                    ('rust', [str(Path(sys.argv[1]).resolve())])]:
                started = time.monotonic()
                process = subprocess.Popen(command, env=environment,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True)
                try:
                    deadline = started + 5
                    while time.monotonic() < deadline:
                        assert process.poll() is None, process.communicate()[1]
                        try:
                            assert call('GetState') == ('idle', 'Ready')
                            break
                        except GLib.Error:
                            time.sleep(.005)
                    else:
                        raise AssertionError('Service startup timed out')
                    startup_ms = (time.monotonic() - started) * 1000
                    time.sleep(.3)
                    values = {}
                    for line in Path(f'/proc/{process.pid}/smaps_rollup').read_text().splitlines():
                        if ':' in line:
                            key, value = line.split(':', 1)
                            if key in ('Rss', 'Pss', 'Private_Dirty'):
                                values[key.lower() + '_kib'] = int(value.split()[0])
                    records.append(dict(implementation=implementation, startup_ms=round(startup_ms, 2), **values))
                    call('Quit')
                    stdout, stderr = process.communicate(timeout=5)
                    assert process.returncode == 0, stderr
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=5)
        summary = {}
        for implementation in ('python', 'rust'):
            summary[implementation] = {
                key: statistics.median(r[key] for r in records if r['implementation'] == implementation)
                for key in ('startup_ms', 'rss_kib', 'pss_kib', 'private_dirty_kib')}
        print(json.dumps({'scope': 'idle service only; no inference/model loaded',
                          'runs': records, 'median': summary}, indent=2))


if __name__ == '__main__':
    main()
