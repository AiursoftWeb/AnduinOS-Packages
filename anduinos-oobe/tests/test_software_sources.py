"""The optional source change probes before writes and isolates mirror failures."""
from importlib.machinery import SourceFileLoader
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

sources = SourceFileLoader('oobe_sources_test', str(
    Path(__file__).parents[1] / 'assets/software_sources.py')).load_module()


class SoftwareSourcesTests(unittest.TestCase):
    def test_each_region_probes_both_sources_before_independent_changes(self):
        for china in (True, False):
            for apt_ok, flatpak_ok in [(True, True), (False, True), (True, False), (False, False)]:
                with self.subTest(china=china, apt=apt_ok, flatpak=flatpak_ok):
                    self.check_switch(china, apt_ok, flatpak_ok)

    def check_switch(self, china, apt_ok, flatpak_ok):
        package = sources.CHINA_CONFIG if china else sources.GLOBAL_CONFIG
        apt_url = sources.CHINA_URL if china else sources.GLOBAL_URL
        flatpak_url = sources.FLATHUB_USTC_URL if china else sources.FLATHUB_OFFICIAL_URL
        events = []

        def run(command, **kwargs):
            if command[:2] == ['dpkg', '--print-architecture']:
                return CompletedProcess(command, 0, 'arm64\n', '')
            if 'install' in command:
                events.append('apt-install')
                self.assertEqual(command[-1], f'/packages/{package}/arm64.deb')
            if 'remote-modify' in command:
                events.append('flatpak-modify')
                self.assertIn(f'--url={flatpak_url}', command)
            return CompletedProcess(command, 0, 'flathub\n', '')

        def apt(path, _work):
            self.assertEqual(path, Path(f'/packages/{package}/arm64.deb'))
            events.append('apt-probe')
            if not apt_ok:
                raise RuntimeError('unreachable')

        def flatpak(url):
            self.assertEqual(url, flatpak_url)
            events.append('flatpak-probe')
            if not flatpak_ok:
                raise RuntimeError('unreachable')

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(sources, '_run', side_effect=run),
            patch.object(sources, 'probe_apt', side_effect=apt),
            patch.object(sources, 'probe_flathub', side_effect=flatpak),
            patch.object(sources, 'china_apt_installed', return_value=not china),
            patch.object(sources, '_apt_package_installed', return_value=True) as installed,
            patch.object(sources, 'SOURCES_FILE', Path(directory) / 'anduinos.sources'),
        ):
            sources.SOURCES_FILE.write_text(f'URIs: {apt_url}\n')
            warnings = sources.switch_sources(china, Path('/packages'))
        self.assertEqual(events[:2], ['apt-probe', 'flatpak-probe'])
        self.assertEqual('apt-install' in events, apt_ok)
        self.assertEqual('flatpak-modify' in events, flatpak_ok)
        self.assertEqual(len(warnings), int(not apt_ok) + int(not flatpak_ok))
        if apt_ok:
            installed.assert_called_once_with(package)

    def test_authorization_failure_does_not_escape_or_prevent_other_source_attempt(self):
        def run(command, **kwargs):
            if command[0] == 'pkexec':
                raise RuntimeError('authorization cancelled')
            return CompletedProcess(command, 0, 'amd64\n' if command[0] == 'dpkg' else 'flathub\n', '')
        with (
            patch.object(sources, '_run', side_effect=run) as commands,
            patch.object(sources, 'probe_apt'),
            patch.object(sources, 'probe_flathub'),
            patch.object(sources, 'china_apt_installed', return_value=False),
        ):
            warnings = sources.switch_sources(True)
        self.assertEqual(len(warnings), 2)
        self.assertEqual(sum(call.args[0][0] == 'pkexec' for call in commands.call_args_list), 2)

    def test_retrying_flathub_does_not_reinstall_existing_apt_configuration(self):
        for china in (True, False):
            with (
                self.subTest(china=china),
                patch.object(sources, '_run', return_value=CompletedProcess([], 0, 'flathub\n', '')) as commands,
                patch.object(sources, 'probe_apt') as probe_apt,
                patch.object(sources, 'probe_flathub'),
                patch.object(sources, 'china_apt_installed', return_value=china),
            ):
                self.assertEqual(sources.switch_sources(china), [])
            probe_apt.assert_not_called()
            self.assertFalse(any('install' in call.args[0] for call in commands.call_args_list))

    def test_probe_uses_only_private_apt_sources_lists_cache_and_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(sources, '_run') as run:
            work = Path(directory)
            sources.probe_apt(Path('/packages/amd64.deb'), work)
            config = (work / 'apt.conf').read_text()
            for suffix in ['payload/etc/apt/sources.list.d/anduinos.sources', 'lists', 'cache']:
                self.assertIn(str(work / suffix), config)
            self.assertIn('Dir::Etc::parts "-"', config)
            self.assertIn('APT::Update::Error-Mode "any"', config)
            self.assertEqual(run.call_args.kwargs['env']['APT_CONFIG'], str(work / 'apt.conf'))
            self.assertNotIn('pkexec', run.call_args.args[0])
