import importlib.machinery
import os
from pathlib import Path
import tempfile
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
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home, NVM_DIR=str(Path(home) / '.nvm')):
            directory = Path(home) / '.local/bin'
            directory.mkdir(parents=True)
            scripts = {
                'node': 'exit 0',
                'npm': 'printf "npm-installed-harness\\n"',
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


if __name__ == '__main__':
    unittest.main()
