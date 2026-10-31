"""Exercise consent, installation feedback and retry on the actual GTK page."""

import importlib.machinery
from pathlib import Path
import threading
import time
import unittest
import warnings
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

    def dialog(self, parent):
        return parent.get_visible_dialog()

    def respond(self, dialog, response):
        button = next(item for item in descendants(dialog)
                      if isinstance(item, oobe.Gtk.Button)
                      and item.get_label() == dialog.get_response_label(response))
        button.emit('clicked')

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
                 'open': lambda: opened.append(True), 'install': install,
                 'get_notice': lambda: {'text': 'Extra information <not markup>',
                                        'providers': [('Example & Co', 'https://example.com/',
                                                       'https://example.com/terms?a=1&b=2',
                                                       'https://example.com/privacy')]}}
        window = None
        factories = [
            lambda: oobe.Gtk.Label(label='Applications'),
            lambda: oobe.create_agents_page(lambda: window._update_nav_buttons()),
            lambda: oobe.Gtk.Label(label='Privacy'),
        ]
        with (patch.object(oobe._agents, 'recommendations', return_value=[agent]),
              patch.object(oobe.OobeWindow, '_get_page_factories', return_value=factories)):
            window = oobe.OobeWindow(None, True)
        page = window._content_page(window._pages[1])
        self.addCleanup(window.destroy)
        window.present()
        window.carousel.scroll_to(window._pages[1], False)
        button = next(item for item in descendants(page) if isinstance(item, oobe.Gtk.Button))
        labels = lambda: [item.get_label() for item in descendants(page)
                          if isinstance(item, oobe.Gtk.Label)]
        progress = next(item for item in descendants(page) if isinstance(item, oobe.Gtk.ProgressBar))
        self.assertIn('Not installed', labels())
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter('always', DeprecationWarning)
            button.emit('clicked')
        self.assertEqual([str(item.message) for item in seen
                          if issubclass(item.category, DeprecationWarning)], [])
        consent = self.dialog(window)
        self.assertEqual(consent.get_default_response(), 'cancel')
        self.assertEqual(consent.get_close_response(), 'cancel')
        self.assertIn('third-party software', consent.get_body())
        self.assertIn('Extra information <not markup>', consent.get_body())
        links = next(item for item in descendants(consent.get_extra_child())
                     if isinstance(item, oobe.Gtk.Label) and item.get_use_markup())
        self.assertEqual(links.get_text(), 'Example & Co · Terms of Service · Privacy Policy')
        self.assertIn('https://example.com/terms?a=1&amp;b=2', links.get_label())
        visited = []
        links.connect('activate-link', lambda _label, uri: visited.append(uri) or True)
        self.assertTrue(links.emit('activate-link', 'https://example.com/terms?a=1&b=2'))
        self.assertEqual(visited, ['https://example.com/terms?a=1&b=2'])
        self.assertEqual(attempts, [])
        self.assertTrue(consent.get_visible())
        self.respond(consent, 'cancel')
        self.assertEqual(attempts, [])
        button.emit('clicked')
        self.dialog(window).close()
        self.wait_for(lambda: window.get_visible_dialog() is None)
        self.assertEqual(attempts, [])
        button.emit('clicked')
        self.respond(self.dialog(window), 'install')
        self.wait_for(lambda: len(attempts) == 1)
        self.assertTrue(page._install_running)
        self.assertTrue(progress.get_visible())
        self.assertFalse(button.get_sensitive())
        self.assertFalse(window.next_btn.get_sensitive())
        self.assertFalse(window.back_btn.get_sensitive())
        self.assertFalse(window.carousel.get_interactive())
        button.emit('clicked')
        self.assertEqual(len(attempts), 1)
        release.set()
        self.wait_for(lambda: not page._install_running)
        self.assertFalse(progress.get_visible())
        self.assertTrue(window.next_btn.get_sensitive())
        self.assertTrue(window.back_btn.get_sensitive())
        self.assertTrue(window.carousel.get_interactive())
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
        self.respond(self.dialog(window), 'install')
        self.wait_for(lambda: len(attempts) == 2 and not page._install_running)
        self.assertIn('Installation failed. Click the agent to retry.', labels())
        button.emit('clicked')
        exitcode = 0
        self.respond(self.dialog(window), 'install')
        self.wait_for(lambda: len(attempts) == 3 and not page._install_running)
        self.assertIn('Installed', labels())
        button.emit('clicked')
        self.assertEqual(opened, [True])

    def test_default_window_shows_two_full_rows_and_half_the_last_row(self):
        for locale in ('zh_CN.UTF-8', 'en_US.UTF-8'):
            for is_oobe in (False, True):
                with self.subTest(locale=locale, is_oobe=is_oobe):
                    factories = [
                        lambda: oobe.Gtk.Label(label='Applications'),
                        lambda: oobe.create_agents_page(),
                        lambda: oobe.Gtk.Label(label='Privacy'),
                    ]
                    with (patch.dict(oobe.os.environ, LANGUAGE=locale),
                          patch.object(oobe.OobeWindow, '_get_page_factories',
                                       return_value=factories)):
                        window = oobe.OobeWindow(None, is_oobe)
                    try:
                        window.present()
                        window.carousel.scroll_to(window._pages[1], False)
                        page = window._content_page(window._pages[1])
                        scroller = next(item for item in descendants(page)
                                        if isinstance(item, oobe.Gtk.ScrolledWindow))
                        buttons = [item for item in descendants(scroller)
                                   if isinstance(item, oobe.Gtk.Button)]
                        self.wait_for(lambda: buttons[-1].get_height() > 0)
                        self.assertEqual(len(buttons), 6)
                        bounds = [button.compute_bounds(scroller)[1] for button in buttons]
                        for card in bounds[:4]:
                            self.assertGreaterEqual(card.get_y(), 0)
                            self.assertLessEqual(card.get_y() + card.get_height(),
                                                 scroller.get_height())
                        for card in bounds[4:]:
                            visible = (scroller.get_height() - card.get_y()) / card.get_height()
                            self.assertGreater(visible, .35)
                            self.assertLess(visible, .75)
                        self.assertTrue(window.next_btn.get_visible())
                        disclaimer = next(item for item in descendants(page)
                                          if isinstance(item, oobe.Gtk.Label)
                                          and item.has_css_class('caption'))
                        footer = disclaimer.compute_bounds(window)[1]
                        navigation = window.next_btn.compute_bounds(window)[1]
                        self.assertLessEqual(footer.get_y() + footer.get_height(),
                                             navigation.get_y())
                    finally:
                        window.destroy()

    def test_locale_selects_only_the_expected_six_recommendations(self):
        for locale, expected in (
            ('zh_CN.UTF-8', ['DeepSeek Harness', 'Qwen Code', 'OpenCode + GLM', 'Kimi Code', 'MiniMax Code', 'CodeBuddy Code']),
            ('zh-CN', ['DeepSeek Harness', 'Qwen Code', 'OpenCode + GLM', 'Kimi Code', 'MiniMax Code', 'CodeBuddy Code']),
            ('en_US.UTF-8', ['OpenCode', 'Claude Code', 'Codex', 'GitHub Copilot', 'Antigravity', 'Grok Build']),
            ('zh_TW.UTF-8', ['OpenCode', 'Claude Code', 'Codex', 'GitHub Copilot', 'Antigravity', 'Grok Build']),
        ):
            with self.subTest(locale=locale), patch.dict(oobe.os.environ, LANGUAGE=locale):
                page = oobe.create_agents_page()
                names = [item.get_label() for item in descendants(page)
                         if isinstance(item, oobe.Gtk.Label) and item.has_css_class('heading')]
                self.assertEqual(names, expected)
                logos = [item for item in descendants(page)
                         if isinstance(item, oobe.Gtk.Image) and item.has_css_class('agent-logo')]
                self.assertEqual(len(logos), 6)
                for logo in logos:
                    # Missing files or one-pixel intrinsic SVGs lose the brand artwork.
                    paintable = logo.get_paintable()
                    self.assertIsNotNone(paintable)
                    self.assertGreater(paintable.get_intrinsic_width(), 1)
                    self.assertGreater(paintable.get_intrinsic_height(), 1)

    def test_continue_offline_removes_the_agent_page_from_the_real_wizard(self):
        factories = [
            lambda: oobe.Gtk.Label(label='Welcome'),
            lambda: oobe.create_agents_page(),
            lambda: oobe.Gtk.Label(label='Privacy'),
        ]
        with patch.object(oobe.OobeWindow, '_get_page_factories', return_value=factories):
            window = oobe.OobeWindow(None, True)
        self.addCleanup(window.destroy)
        agent_page = window._pages[1]
        window._continue_offline()
        self.assertNotIn(agent_page, window._pages)
        self.assertEqual(window.carousel.get_n_pages(), 2)

    def test_deepseek_confirmation_discloses_optional_node_installation(self):
        with patch.dict(oobe.os.environ, LANGUAGE='zh_CN'):
            page = oobe.create_agents_page()
        window = oobe.Adw.Window()
        self.addCleanup(window.destroy)
        window.set_content(page)
        window.present()
        buttons = [item for item in descendants(page) if isinstance(item, oobe.Gtk.Button)]
        with patch.object(oobe._agents, 'executable', return_value=None):
            buttons[0].emit('clicked')
        dialog = self.dialog(window)
        self.assertIn('latest Node.js release', dialog.get_body())
        self.assertIn('nvm', dialog.get_body())
        self.assertIn('DeepSeek Harness', dialog.get_extra_child().get_last_child().get_text())
        self.respond(dialog, 'cancel')
        self.wait_for(lambda: window.get_visible_dialog() is None)
        with patch.object(oobe._agents, 'executable', return_value=None):
            buttons[2].emit('clicked')
        dialog = self.dialog(window)
        self.assertIn('GLM is a separate model service', dialog.get_body())
        links = [item.get_text() for item in descendants(dialog.get_extra_child())
                 if isinstance(item, oobe.Gtk.Label) and item.get_use_markup()]
        self.assertEqual(links, ['OpenCode · Terms of Service · Privacy Policy',
                                 'Z.AI / GLM · Terms of Service · Privacy Policy'])
        self.respond(dialog, 'cancel')


if __name__ == '__main__':
    unittest.main()
