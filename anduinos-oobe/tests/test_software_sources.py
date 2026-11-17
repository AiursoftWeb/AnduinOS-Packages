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
    def test_both_probes_finish_before_either_change_and_fail_independently(self):
        for apt_ok, flatpak_ok in [(True, True), (False, True), (True, False), (False, False)]:
            with self.subTest(apt=apt_ok, flatpak=flatpak_ok):
                events = []
                installed = [False]
                def run(command, **kwargs):
                    if command[:2] == ['dpkg', '--print-architecture']:
                        return CompletedProcess(command, 0, 'arm64\n', '')
                    if command[0] == 'pkexec':
                        events.append('change')
                        if 'install' in command:
                            self.assertEqual(command[-1], '/packages/arm64.deb')
                            installed[0] = True
                    return CompletedProcess(command, 0, 'flathub\n', '')
                def apt(*args):
                    events.append('apt-probe')
                    if not apt_ok:
                        raise RuntimeError('unreachable')
                def flatpak():
                    events.append('flatpak-probe')
                    if not flatpak_ok:
                        raise RuntimeError('unreachable')
                with patch.object(sources, '_run', side_effect=run) as commands, patch.object(
                    sources, 'probe_apt', side_effect=apt), patch.object(
                    sources, 'probe_flathub', side_effect=flatpak), patch.object(
                    sources, 'china_apt_configured', side_effect=lambda: installed[0]):
                    warnings = sources.switch_to_china(Path('/packages'))
                self.assertEqual(events[:2], ['apt-probe', 'flatpak-probe'])
                calls = [call.args[0] for call in commands.call_args_list]
                self.assertEqual(any('install' in command for command in calls), apt_ok)
                self.assertEqual(any('remote-modify' in command for command in calls), flatpak_ok)
                self.assertEqual(bool(warnings), not (apt_ok and flatpak_ok))

    def test_authorization_failure_does_not_escape_or_prevent_other_source_attempt(self):
        def run(command, **kwargs):
            if command[0] == 'pkexec':
                raise RuntimeError('authorization cancelled')
            return CompletedProcess(command, 0, 'amd64\n' if command[0] == 'dpkg' else 'flathub\n', '')
        with patch.object(sources, '_run', side_effect=run) as commands, patch.object(
            sources, 'probe_apt'), patch.object(sources, 'probe_flathub'), patch.object(
            sources, 'china_apt_configured', return_value=False):
            warnings = sources.switch_to_china()
        self.assertEqual(len(warnings), 2)
        self.assertEqual(sum(call.args[0][0] == 'pkexec' for call in commands.call_args_list), 2)

    def test_already_installed_china_package_is_not_reinstalled(self):
        with patch.object(sources, '_run', return_value=CompletedProcess([], 0, 'amd64\n', '')) as commands, patch.object(
            sources, 'probe_apt'), patch.object(sources, 'probe_flathub'), patch.object(
            sources, 'china_apt_configured', return_value=True):
            self.assertEqual(sources.switch_to_china(), [])
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
