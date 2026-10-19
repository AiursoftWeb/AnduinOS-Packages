"""Exercise consent, installation feedback and retry on the actual GTK page."""

import importlib.machinery
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
oobe = importlib.machinery.SourceFileLoader(
    'oobe_agents_page_tests', str(ROOT / 'assets/anduinos-oobe')).load_module()


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


class AgentsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not oobe.Gtk.init_check():
            raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
        oobe.Adw.init()

    def wait_for(self, condition):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            while oobe.GLib.MainContext.default().pending():
                oobe.GLib.MainContext.default().iteration(False)
            if condition():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for the page state')

    def dialog(self):
        return next(window for window in oobe.Gtk.Window.get_toplevels()
                    if isinstance(window, oobe.Adw.MessageDialog))

    def test_consent_progress_output_retry_and_open(self):
        installed = False
        exitcode = 0
        attempts = []
        opened = []
        release = threading.Event()
        self.addCleanup(release.set)

        def install(output):
            attempts.append(True)
            output('$ installer\nstdout\nstderr\n')
            release.wait(5)
            return exitcode

        agent = {'id': 'example', 'name': 'Example', 'description': 'Coding assistant',
                 'is_installed': lambda: installed,
                 'open': lambda: opened.append(True), 'install': install}
        with patch.object(oobe._agents, 'recommendations', return_value=[agent]):
            page = oobe.create_agents_page()
        window = oobe.Adw.Window()
        self.addCleanup(window.destroy)
        window.set_content(page)
        window.present()
        button = next(item for item in descendants(page) if isinstance(item, oobe.Gtk.Button))
        labels = lambda: [item.get_label() for item in descendants(page)
                          if isinstance(item, oobe.Gtk.Label)]
        progress = next(item for item in descendants(page) if isinstance(item, oobe.Gtk.ProgressBar))
        self.assertIn('Not installed', labels())
        button.emit('clicked')
        consent = self.dialog()
        self.assertIn('third-party software', consent.get_body())
        consent.response('cancel')
        self.assertEqual(attempts, [])
        button.emit('clicked')
        self.dialog().response('install')
        self.wait_for(lambda: len(attempts) == 1)
        self.assertTrue(page._install_running)
        self.assertTrue(progress.get_visible())
        self.assertFalse(button.get_sensitive())
        button.emit('clicked')
        self.assertEqual(len(attempts), 1)
        release.set()
        self.wait_for(lambda: not page._install_running)
        self.assertFalse(progress.get_visible())
        self.assertIn('Installation failed. Click the agent to retry.', labels())
        expander = next(item for item in descendants(page) if isinstance(item, oobe.Gtk.Expander))
        self.assertTrue(expander.get_expanded())
        textview = next(item for item in descendants(page) if isinstance(item, oobe.Gtk.TextView))
        buffer = textview.get_buffer()
        log = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        self.assertIn('$ installer\nstdout\nstderr\n', log)
        self.assertIn('executable could not be found', log)
        button.emit('clicked')
        installed = True
        exitcode = 7
        self.dialog().response('install')
        self.wait_for(lambda: len(attempts) == 2 and not page._install_running)
        self.assertIn('Installation failed. Click the agent to retry.', labels())
        button.emit('clicked')
        exitcode = 0
        self.dialog().response('install')
        self.wait_for(lambda: len(attempts) == 3 and not page._install_running)
        self.assertIn('Installed', labels())
        button.emit('clicked')
        self.assertEqual(opened, [True])

    def test_locale_selects_only_the_expected_five_recommendations(self):
        for locale, expected in (
            ('zh_CN.UTF-8', ['Kimi Code', 'DeepSeek Harness', 'GLM · OpenCode', 'MiniMax Code', 'Qwen Code']),
            ('zh-CN', ['Kimi Code', 'DeepSeek Harness', 'GLM · OpenCode', 'MiniMax Code', 'Qwen Code']),
            ('en_US.UTF-8', ['Claude Code', 'Codex', 'GitHub Copilot', 'Antigravity', 'OpenCode']),
            ('zh_TW.UTF-8', ['Claude Code', 'Codex', 'GitHub Copilot', 'Antigravity', 'OpenCode']),
        ):
            with self.subTest(locale=locale), patch.dict(oobe.os.environ, LANGUAGE=locale):
                page = oobe.create_agents_page()
                names = [item.get_label() for item in descendants(page)
                         if isinstance(item, oobe.Gtk.Label) and item.has_css_class('heading')]
                self.assertEqual(names, expected)


if __name__ == '__main__':
    unittest.main()
