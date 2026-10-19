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
    def test_user_install_is_detected_without_restarting_the_desktop(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home):
            binary = Path(home) / '.local/bin/kimi'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            recommendation = agents.recommendations(lambda text: text, True)[0]
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
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home):
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
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, HOME=home):
            output = []
            code = agents.install_agent(
                'mkdir -p "$HOME/.local/bin"; '
                'printf "#!/bin/sh\\nexit 0\\n" > "$HOME/.local/bin/kimi"; '
                'chmod +x "$HOME/.local/bin/kimi"; '
                'if read -r input; then exit 9; fi; printf "installed\\n"',
                output.append)
            self.assertEqual(code, 0)
            self.assertTrue(agents.recommendations(lambda text: text, True)[0]['is_installed']())
            self.assertIn('installed\n', output)


if __name__ == '__main__':
    unittest.main()
