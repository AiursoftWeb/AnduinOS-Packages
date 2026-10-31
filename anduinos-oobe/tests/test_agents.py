import importlib.machinery
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
agents = importlib.machinery.SourceFileLoader(
    'oobe_agents_tests', str(ROOT / 'assets/agents.py')).load_module()


class AgentOperationsTests(unittest.TestCase):
    def test_recommendation_order_and_deepseek_runtime_notice(self):
        chinese = agents.recommendations(lambda text: text, True)
        international = agents.recommendations(lambda text: text, False)
        self.assertEqual([item['id'] for item in chinese],
                         ['deepseek', 'qwen', 'glm', 'kimi', 'minimax', 'codebuddy'])
        self.assertEqual([item['id'] for item in international],
                         ['opencode', 'claude', 'codex', 'copilot', 'antigravity', 'grok'])
        self.assertEqual(chinese[2]['name'], 'OpenCode + GLM')
        self.assertIn('Node.js', chinese[0]['get_notice']()['text'])
        self.assertFalse(any('Node.js' in item['get_notice']()['text'] for item in chinese[1:]))
        self.assertEqual([provider[0] for provider in chinese[2]['get_notice']()['providers']],
                         ['OpenCode', 'Z.AI / GLM'])
        for item in chinese + international:
            notice = item['get_notice']()
            self.assertTrue(notice['providers'])
            for name, website, terms, privacy in notice['providers']:
                self.assertTrue(name)
                self.assertTrue(all(url.startswith('https://') for url in (website, terms, privacy)))

    def test_grok_install_is_found_without_restarting_the_desktop(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home):
            binary = Path(home) / '.grok/bin/grok'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            terminal = binary.parent / 'ptyxis'
            terminal.write_text('#!/bin/sh\nexit 0\n')
            terminal.chmod(0o755)
            item = agents.recommendations(lambda text: text, False)[-1]
            self.assertTrue(item['is_installed']())
            with patch.object(agents.subprocess, 'Popen') as launch:
                item['open']()
            self.assertEqual(launch.call_args.args[0][-1], str(binary))

    def test_user_install_is_detected_without_restarting_the_desktop(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home, NVM_DIR=str(Path(home) / '.nvm')):
            binary = Path(home) / '.local/bin/kimi'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            recommendation = next(item for item in agents.recommendations(lambda text: text, True)
                                  if item['id'] == 'kimi')
            self.assertFalse(recommendation['is_installed']())
            binary.chmod(0o755)
            self.assertTrue(recommendation['is_installed']())
            terminal = binary.parent / 'ptyxis'
            terminal.write_text('#!/bin/sh\nexit 0\n')
            terminal.chmod(0o755)
            with patch.object(agents.subprocess, 'Popen') as launch:
                recommendation['open']()
            command = launch.call_args.args[0]
            self.assertEqual(command[:6],
                             ['ptyxis', '--new-window', '--working-directory', home,
                              '--', '/usr/bin/env'])
            self.assertTrue(command[6].startswith('PATH='))
            self.assertEqual(command[-1], str(binary))
            self.assertEqual(launch.call_args.kwargs['cwd'], home)

    def test_open_uses_gnome_terminal_when_ptyxis_is_unavailable(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home, NVM_DIR=str(Path(home) / '.nvm')):
            directory = Path(home) / '.local/bin'
            directory.mkdir(parents=True)
            for name in ('kimi', 'gnome-terminal'):
                path = directory / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            with (patch.object(agents.shutil, 'which',
                               side_effect=lambda command, **kwargs:
                               str(directory / command) if command in ('kimi', 'gnome-terminal') else None),
                  patch.object(agents.subprocess, 'Popen') as launch):
                agents.open_agent('kimi')
            self.assertEqual(launch.call_args.args[0][:2],
                             ['gnome-terminal', '--working-directory'])
            self.assertEqual(launch.call_args.args[0][-1], str(directory / 'kimi'))

    def test_installer_streams_command_stdout_stderr_and_reports_pipeline_failure(self):
        output = []
        code = agents.install_agent(
            "printf 'download failed\\n' >&2; false | cat", output.append)
        self.assertNotEqual(code, 0)
        self.assertTrue(output[0].startswith('$ bash -o pipefail -c '))
        self.assertIn('download failed\n', output)

    def test_installation_uses_current_user_home_and_noninteractive_stdin(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home, NVM_DIR=str(Path(home) / '.nvm')):
            output = []
            code = agents.install_agent(
                'mkdir -p "$HOME/.local/bin"; '
                'printf "#!/bin/sh\\nexit 0\\n" > "$HOME/.local/bin/kimi"; '
                'chmod +x "$HOME/.local/bin/kimi"; '
                'if read -r input; then exit 9; fi; printf "installed\\n"',
                output.append)
            self.assertEqual(code, 0)
            self.assertTrue(next(item for item in agents.recommendations(lambda text: text, True)
                                  if item['id'] == 'kimi')['is_installed']())
            self.assertIn('installed\n', output)

    def test_deepseek_reuses_a_compatible_runtime_without_downloading_nvm(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(
                os.environ, HOME=home, NVM_DIR=str(Path(home) / '.nvm'), XDG_DATA_HOME=home + '/data'):
            directory = Path(home) / '.local/bin'
            directory.mkdir(parents=True)
            scripts = {
                'node': 'exit 0',
                'npm': 'printf "#!/bin/sh\\nexit 0\\n" > "$HOME/.local/bin/dsh"; '
                       'chmod +x "$HOME/.local/bin/dsh"; printf "npm-installed-harness\\n"',
                'curl': 'printf "unexpected-runtime-download\\n"; exit 9',
            }
            for name, body in scripts.items():
                path = directory / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            agent = next(item for item in agents.recommendations(lambda text: text, True)
                         if item['id'] == 'deepseek')
            output = []
            self.assertEqual(agent['install'](output.append), 0)
            self.assertIn('npm-installed-harness\n', output)
            self.assertNotIn('unexpected-runtime-download\n', output)

    def test_successful_installs_create_searchable_entries_for_both_lists(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(
                os.environ, HOME=home, NVM_DIR=home + '/.nvm', XDG_DATA_HOME=home + '/user data'):
            directory = Path(home) / '.local/bin'
            directory.mkdir(parents=True)
            for command in ('dsh', 'qwen', 'opencode', 'kimi', 'mcode', 'codebuddy',
                            'claude', 'codex', 'copilot', 'agy', 'grok'):
                binary = directory / command
                binary.write_text('#!/bin/sh\nexit 0\n')
                binary.chmod(0o755)
            for chinese in (False, True):
                for item in agents.recommendations(lambda text: text, chinese):
                    with self.subTest(agent=item['id']):
                        output = []
                        self.assertEqual(agents.install_agent('true', output.append, item['id']), 0)
                        entry = Path(home) / 'user data/applications' / (
                            'anduinos-agent-' + item['id'] + '.desktop')
                        text = entry.read_text()
                        self.assertIn('Name=' + item['name'] + '\n', text)
                        self.assertIn('Icon=' + str(ROOT / 'resources/icons/agents' /
                                                 (item['id'] + '.svg')) + '\n', text)
                        self.assertIn('--launch ' + item['id'] + '\n', text)
                        self.assertTrue(any('Created application menu entry:' in line for line in output))
                        self.assertEqual(agents.install_agent('true', output.append, item['id']), 0)
                        self.assertEqual(entry.read_text(), text)

    def test_failed_or_incomplete_install_does_not_create_a_menu_entry(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(
                os.environ, HOME=home, NVM_DIR=home + '/.nvm', XDG_DATA_HOME=home + '/data', PATH='/usr/bin'):
            output = []
            self.assertNotEqual(agents.install_agent('false | cat', output.append, 'kimi'), 0)
            self.assertFalse(any('create_aitool_shortcut.py' in line for line in output))
            self.assertNotEqual(agents.install_agent('true', output.append, 'kimi'), 0)
            self.assertIn('The agent executable could not be found.\n', output)
            self.assertFalse((Path(home) / 'data/applications/anduinos-agent-kimi.desktop').exists())

    def test_shortcut_defaults_to_user_menu_and_launches_with_updated_nvm_runtime(self):
        with tempfile.TemporaryDirectory(prefix='oobe home ') as home, patch.dict(
                os.environ, HOME=home, NVM_DIR=home + '/.nvm', XDG_DATA_HOME='', PATH='/usr/bin'):
            directory = Path(home) / '.local/bin'
            directory.mkdir(parents=True)
            terminal = directory / 'ptyxis'
            terminal.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            terminal.chmod(0o755)
            runtime = Path(home) / '.nvm/versions/node/v24.0.0/bin'
            runtime.mkdir(parents=True)
            binary = runtime / 'dsh'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            self.assertEqual(agents.install_agent('true', lambda text: None, 'deepseek'), 0)
            entry = Path(home) / '.local/share/applications/anduinos-agent-deepseek.desktop'
            self.assertTrue(entry.is_file())
            newer = runtime.parent.parent / 'v25.0.0/bin'
            newer.mkdir(parents=True)
            binary.rename(newer / 'dsh')
            result = subprocess.run(
                ['/usr/bin/python3', str(ROOT / 'assets/create_aitool_shortcut.py'), '--launch', 'deepseek'],
                capture_output=True, text=True, check=True, timeout=15)
            self.assertIn(str(newer / 'dsh') + '\nweb\n', result.stdout)
            self.assertIn('--new-window\n--working-directory\n' + home + '\n', result.stdout)


if __name__ == '__main__':
    unittest.main()
