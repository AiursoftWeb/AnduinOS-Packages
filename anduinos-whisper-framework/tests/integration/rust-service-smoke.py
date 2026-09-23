#!/usr/bin/env python3
"""Real Rust service on an isolated bus; never invokes microphone methods.

Run through dbus-run-session. Tests use private schema/cache directories and
retain Python solely as a test client, not as the backend implementation.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from gi.repository import Gio, GLib

APP = 'com.anduinos.VoiceTyping'
OBJECT = '/com/anduinos/VoiceTyping'
ROOT = Path(__file__).resolve().parents[2]


def bus_connection():
    return Gio.DBusConnection.new_for_address_sync(
        os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
        Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def bus_call(connection, method, signature, args):
    return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                'org.freedesktop.DBus', method,
                                GLib.Variant(signature, args), None,
                                Gio.DBusCallFlags.NONE, 2000, None).unpack()


def call(connection, method, parameters=None, interface=APP):
    return connection.call_sync(APP, OBJECT, interface, method, parameters, None,
                                Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()


def wait_ready(connection, process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(process.communicate()[1])
        try:
            return call(connection, 'GetState')
        except GLib.Error:
            time.sleep(.02)
    raise AssertionError('Rust D-Bus service did not appear')


def main():
    if os.environ.get('ANDUINOS_ISOLATED_TEST_BUS') != '1':
        raise SystemExit('Use dbus-run-session with ANDUINOS_ISOLATED_TEST_BUS=1')
    binary = str(Path(sys.argv[1]).resolve())
    diagnostics = str(Path(sys.argv[2]).resolve())
    shell = bus_connection()
    assert bus_call(shell, 'RequestName', '(su)', ('org.gnome.Shell', 4)) == (1,)
    outsider = bus_connection()
    with tempfile.TemporaryDirectory(prefix='voice-rust-service-') as directory:
        environment = dict(os.environ, GSETTINGS_BACKEND='memory',
                           GSETTINGS_SCHEMA_DIR=directory, XDG_CACHE_HOME=directory,
                           XDG_RUNTIME_DIR=directory, PIPEWIRE_RUNTIME_DIR=directory,
                           PIPEWIRE_REMOTE='voice-test-no-server', GIO_USE_VFS='local',
                           GTK_A11Y='none', NO_AT_BRIDGE='1')
        schema = ROOT / 'data/com.anduinos.voice-typing.gschema.xml'
        schema_tree = ET.parse(schema)
        for key, value in [('audio-cues', 'false'), ('recognition-backend', "'cpu'")]:
            schema_tree.find(f"schema/key[@name='{key}']/default").text = value
        schema_tree.write(Path(directory, schema.name))
        subprocess.run(['glib-compile-schemas', directory], check=True)
        processes = []

        def launch():
            process = subprocess.Popen([binary], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True)
            processes.append(process)
            return process

        def clean_exit(process):
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, (process.returncode, stderr)
            assert not stdout, stdout
            assert not stderr, stderr  # Include GLib criticals, not just exit code.

        try:
            before = subprocess.run([diagnostics], env=environment, capture_output=True, text=True)
            assert before.returncode == 1 and 'No compatible running voice session' in before.stderr
            assert bus_call(shell, 'NameHasOwner', '(s)', (APP,)) == (False,)
            process = launch()
            assert wait_ready(shell, process) == ('idle', 'Ready')
            for method in ('Start', 'Stop', 'Finish', 'Quit', 'ReportDelivery'):
                try:
                    call(outsider, method, GLib.Variant('(u)', (1,))
                         if method == 'ReportDelivery' else None)
                except GLib.Error as error:
                    assert APP + '.AccessDenied' in str(error), error
                else:
                    raise AssertionError(f'Unauthorized {method} succeeded')
            assert call(outsider, 'GetState') == ('idle', 'Ready')
            report = json.loads(call(outsider, 'GetDiagnostics')[0])
            assert report == {'schema_version': 1, 'measurements': []}, report
            exported = subprocess.run([diagnostics], env=environment, capture_output=True, text=True)
            assert exported.returncode == 0 and json.loads(exported.stdout) == report
            interface = ET.fromstring(call(shell, 'Introspect',
                                          interface='org.freedesktop.DBus.Introspectable')[0])
            methods = {m.attrib['name'] for m in interface.find(f"interface[@name='{APP}']").findall('method')}
            assert methods == {'Start', 'Stop', 'Finish', 'Quit', 'StartTest',
                               'StopTest', 'ReportDelivery', 'GetState', 'GetDiagnostics'}
            # Compare the complete wire contract, not just method names. Read
            # the frozen reference constant without importing/running its service.
            reference_tree = ast.parse((ROOT / 'tests/reference/anduinos_whisper_framework/daemon.py').read_text())
            reference_value = next(node.value for node in reference_tree.body
                                   if isinstance(node, ast.Assign) and
                                   any(isinstance(target, ast.Name) and target.id == 'INTROSPECTION_XML'
                                       for target in node.targets))
            assert isinstance(reference_value, ast.JoinedStr)
            reference_parts = []
            for part in reference_value.values:
                if isinstance(part, ast.Constant):
                    reference_parts.append(part.value)
                else:
                    assert isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                    assert part.value.id == 'INTERFACE'
                    reference_parts.append(APP)
            reference_xml = ''.join(reference_parts)
            def signatures(node):
                contract = node.find(f"interface[@name='{APP}']")
                return {(entry.tag, entry.attrib['name']):
                        tuple((arg.attrib['type'], arg.get('direction', 'in' if entry.tag == 'method' else 'out'))
                              for arg in entry.findall('arg'))
                        for entry in contract if entry.tag in ('method', 'signal')}
            assert signatures(interface) == signatures(ET.fromstring(reference_xml))
            call(shell, 'Stop')
            call(shell, 'Finish')
            call(shell, 'ReportDelivery', GLib.Variant('(u)', (42,)))
            duplicate = launch()
            clean_exit(duplicate)
            assert process.poll() is None
            assert call(shell, 'GetState') == ('idle', 'Ready')
            children = set()
            if '--native' in sys.argv:
                call(shell, 'Start')
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    children.update(Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split())
                    # Recognition worker lives on another thread. Linux lists
                    # children under the thread which spawned them.
                    for task in Path(f'/proc/{process.pid}/task').iterdir():
                        try:
                            children.update((task / 'children').read_text().split())
                        except FileNotFoundError:
                            pass
                    if children:
                        break
                    time.sleep(.01)
                assert children, 'No native worker started during preparation'
                call(shell, 'Finish')
                assert call(shell, 'GetState') == ('idle', 'Ready')
                time.sleep(.3)
                assert call(shell, 'GetState') == ('idle', 'Ready'), 'Cancelled preparation resumed'
                call(shell, 'Start')
                call(shell, 'Stop')
                assert call(shell, 'GetState') == ('idle', 'Ready')
            call(shell, 'Quit')
            clean_exit(process)
            assert not any(Path('/proc', pid).exists() for pid in children), 'Worker survived service shutdown'

            process = launch()
            wait_ready(shell, process)
            bus_call(shell, 'ReleaseName', '(s)', ('org.gnome.Shell',))
            clean_exit(process)

            # No Shell owner at startup must also exit rather than linger.
            clean_exit(launch())
            print('Rust service: authorization, introspection, diagnostics, duplicate instance, Quit and Shell loss passed')
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()


if __name__ == '__main__':
    main()
