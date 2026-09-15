"""Cross-frontend settings and real multi-engine clear protocol, in temp homes."""
import importlib.util
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('settings', ROOT.parent / 'lib/bash_prediction_settings.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


class SettingsTests(unittest.TestCase):
    def test_legacy_migration_no_shell_execution_and_default_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            marker = home / 'must-not-exist'
            bashrc = home / '.bashrc'
            contents = f'export ANDUINOS_GUESS_COMMAND=0\nexport ANDUINOS_GUESS_PERSIST=1\ntouch {marker}\n'
            bashrc.write_text(contents)
            self.assertEqual(settings.read_settings(home), dict(enabled=False, history=True, persist=True))
            settings.set_enabled(True, home)
            self.assertTrue(settings.read_settings(home)['enabled'])
            self.assertFalse(marker.exists())
            self.assertEqual(bashrc.read_text(), contents)
            path = settings.config_path(home)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            settings.restore_defaults(home)
            self.assertEqual(settings.read_settings(home), settings.DEFAULTS)

    def test_default_remembers_habits_but_preserves_explicit_opt_out(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings.read_settings(directory), dict(enabled=True, history=True, persist=True))
            settings.save_settings(dict(enabled=True, history=True, persist=False), directory)
            self.assertFalse(settings.read_settings(directory)['persist'])
            settings.restore_defaults(directory)
            self.assertTrue(settings.read_settings(directory)['persist'])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ANDUINOS_GUESS_PERSIST':'0'}):
            self.assertFalse(settings.read_settings(directory)['persist'])

    def test_history_disabled_disables_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            settings.save_settings(dict(enabled=True, history=False, persist=True), directory)
            self.assertFalse(settings.read_settings(directory)['persist'])

    def test_clear_and_reset_have_separate_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bash_history = home / '.bash_history'
            bash_history.write_text('git status\n')
            values = dict(enabled=False, history=True, persist=True)
            settings.save_settings(values, home)
            state = settings.state_directory(home)
            state.mkdir(parents=True)
            (state / 'history-v1').write_text('old learning')
            settings.restore_defaults(home)
            self.assertTrue((state / 'history-v1').exists())
            settings.save_settings(values, home)
            settings.clear_learning(home)
            self.assertFalse((state / 'history-v1').exists())
            self.assertEqual(settings.read_settings(home), values)
            self.assertEqual(bash_history.read_text(), 'git status\n')

    def test_invalid_config_is_data_not_code(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            p = settings.config_path(home); p.parent.mkdir(parents=True)
            p.write_text('enabled=$(touch /tmp/not-executed)\nhistory=0\npersist=1\n')
            self.assertEqual(settings.read_settings(home), dict(enabled=True, history=False, persist=False))

    def test_real_bash_configuration_precedence_and_new_terminals(self):
        module = os.environ.get('ANDUINOS_GHOST_MODULE')
        engine = os.environ.get('ANDUINOS_QUIETD')
        if not module or not engine:
            self.skipTest('Run via test-package.sh with native artifacts')
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            package = home / 'package'
            package.mkdir()
            shutil.copy(module, package / 'anduinos-ghost.so')
            shutil.copy(engine, package / 'anduinos-quietd')
            loader = package / 'loader'
            loader.write_text((ROOT / 'assets/anduinos-bash-guess-command').read_text().replace(
                '/usr/lib/anduinos-bash-guess-command', str(package)))
            settings.save_settings(dict(enabled=True, history=False, persist=False), home)
            env = {k: v for k, v in os.environ.items() if not k.startswith(('XDG_', 'ANDUINOS_'))}
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'), TERM='xterm',
                       ANDUINOS_GUESS_COMMAND='0', ANDUINOS_GUESS_HISTORY='1',
                       ANDUINOS_QUIETD=str(package / 'anduinos-quietd'))
            output = home / 'diagnose'
            config = settings.config_path(home)
            def shell(code):
                result = subprocess.run(['script', '-qefc', shlex.join([
                    'bash', '--noprofile', '--norc', '-ic', code]), '/dev/null'],
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            shell(f"HISTFILE=/dev/null; source {shlex.quote(str(loader))}; "
                  f"anduinos_ghost diagnose > {shlex.quote(str(output))}; "
                  f"printf 'enabled=0\\nhistory=0\\npersist=0\\n' > {shlex.quote(str(config))}; "
                  f"source {shlex.quote(str(loader))}; "
                  "[[ $_ANDUINOS_GUESS_CONFIG_COMMAND == 1 && $_ANDUINOS_GUESS_CONFIG_HISTORY == 0 ]]")
            self.assertIn('enabled=1 installed=1', output.read_text())
            shell(f"source {shlex.quote(str(loader))}; "
                  "[[ $_ANDUINOS_GUESS_CONFIG_COMMAND == 0 ]] && ! type -t anduinos_ghost")

    def test_old_engines_cannot_resurrect_cleared_learning(self):
        engine = os.environ.get('ANDUINOS_QUIETD')
        if not engine:
            self.skipTest('Run via test-package.sh with the freshly built engine')
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {k:v for k,v in os.environ.items() if not k.startswith(('XDG_', 'ANDUINOS_GUESS_', 'ANDUINOS_BASH_'))}
            env.update(HOME=str(home), ANDUINOS_GUESS_HISTORY='1', ANDUINOS_BASH_HISTFILE='')
            engines = []
            def start():
                p = subprocess.Popen([engine, '--fixture-bin-dir', str(ROOT/'tests/fixtures/runtime-bin')],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                engines.append(p)
                request(p, 'P\n', 'P')
                return p
            def request(p, text, expected='A'):
                p.stdin.write(text);p.stdin.flush()
                self.assertTrue(select.select([p.stdout], [], [], 5)[0], 'engine timeout')
                self.assertEqual(p.stdout.readline().strip(), expected)
            def observe(p, command):
                request(p, f'O\t0\t{int(time.time()*1000)}\t{command.encode().hex()}\t{str(home).encode().hex()}\n')
            def until(condition):
                deadline=time.monotonic()+5
                while not condition() and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(condition())
            try:
                first, second = start(), start()
                observe(first, 'echo before-one');observe(second, 'echo before-two')
                state=settings.state_directory(home)
                until(lambda:(state/'history-v1').exists())
                settings.clear_learning(home)
                for p in (first,second):
                    observe(p, 'echo stale-after-clear');observe(p, 'echo stale-transition')
                # Queued work drains on process shutdown; no timing assumption.
                for p in (first,second):
                    p.stdin.write('X\n');p.stdin.flush();p.wait(timeout=10)
                self.assertFalse((state/'history-v1').exists())
                self.assertFalse((state/'transitions-v1').exists())
                fresh=start();observe(fresh, 'echo new-session')
                until(lambda:(state/'history-v1').exists())
                self.assertNotIn('stale-after-clear'.encode().hex(),(state/'history-v1').read_text())
            finally:
                for p in engines:
                    if p.poll() is None:p.terminate()
                    p.wait(timeout=10)
                    p.stdin.close();p.stdout.close()

if __name__ == '__main__': unittest.main()
