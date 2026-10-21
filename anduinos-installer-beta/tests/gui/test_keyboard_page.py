"""Session layout confirmation, wizard order, and native GTK input regression."""

import ctypes
import ctypes.util
import os
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

import pages
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from keyboard_session import KeyboardSessionError, apply_keyboard_layout
from languages import LANGUAGES


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


def settle(seconds=0.2):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


class KeyboardPageTests(unittest.TestCase):
    def setUp(self):
        self.apply = self.enterContext(patch('pages.apply_keyboard_layout', return_value={51: {0: 36}}))
        self.active = self.enterContext(patch('pages.keyboard_layout_is_active', return_value=True))

    def build(self, **values):
        self.shared = {'lang': 'en_US', 'keyboard': 'ch', 'keyboard_variant': '',
                       '_page_route_initialized': True, '_network_page_planned': True,
                       **values}
        self.window = Adw.Window(default_width=960, default_height=740)
        self.addCleanup(self.window.destroy)
        self.nav = Adw.NavigationView()
        self.page = pages.build_keyboard_page(self.shared, self.nav)
        self.nav.add(self.page)
        self.window.set_content(self.nav)
        self.window.present()
        self.entry = next(w for w in descendants(self.page) if isinstance(w, Gtk.Entry))
        self.footer = next(w for w in descendants(self.page) if w.has_css_class('wizard-navigation'))
        return self.page

    def test_next_is_blocked_until_compositor_confirms_layout(self):
        self.active.return_value = False
        self.build()
        settle()
        self.assertFalse(self.entry.get_sensitive())
        self.assertFalse(self.footer.next_button.get_sensitive())
        self.apply.assert_called_with('ch', '')
        self.active.return_value = True
        settle()
        self.assertTrue(self.entry.get_sensitive())
        self.assertTrue(self.footer.next_button.get_sensitive())

    def test_failure_and_retry_do_not_allow_password_entry_with_old_layout(self):
        self.apply.side_effect = KeyboardSessionError('locked')
        self.build()
        settle()
        retry = next(w for w in descendants(self.page)
                     if isinstance(w, Gtk.Button) and w.get_label() == 'Retry')
        self.assertTrue(retry.get_visible())
        self.assertFalse(self.footer.next_button.get_sensitive())
        self.apply.side_effect = None
        retry.emit('clicked')
        settle()
        self.assertFalse(retry.get_visible())
        self.assertTrue(self.footer.next_button.get_sensitive())

    def test_keyboard_next_routes_to_network_or_software(self):
        for needs_network in (True, False):
            with self.subTest(network=needs_network):
                self.build(_network_page_planned=needs_network)
                settle()
                destination = Adw.NavigationPage(title='Next')
                with (patch('pages.build_network_page', return_value=destination) as network,
                      patch('pages.build_software_page', return_value=destination) as software):
                    self.footer.next_button.emit('clicked')
                self.assertIs(self.nav.get_visible_page(), destination)
                self.assertEqual(network.call_count, int(needs_network))
                self.assertEqual(software.call_count, int(not needs_network))
                self.assertTrue(self.shared['_keyboard_user_selected'])
                self.window.destroy()

    def test_manual_layout_and_variant_are_applied_and_survive_language_changes(self):
        self.build(keyboard='us')
        dropdowns = [w for w in descendants(self.page) if isinstance(w, Gtk.DropDown)]
        layout, variant = dropdowns
        index = next(i for i, item in enumerate(pages.keyboard_layouts()) if item.id == 'ch')
        layout.set_selected(index)
        variant.set_selected(1)
        settle()
        selected_variant = pages.keyboard_layouts()[index].variants[0].id
        self.apply.assert_called_with('ch', selected_variant)
        self.assertTrue(self.shared['_keyboard_user_selected'])
        welcome = pages.build_welcome_page(self.shared, self.nav)
        languages = next(w for w in descendants(welcome) if isinstance(w, Gtk.ListView))
        languages.get_model().set_selected(next(i for i, language in enumerate(LANGUAGES)
                                               if language.code == 'fr'))
        self.assertEqual(self.shared['lang'], 'fr')
        self.assertEqual(self.shared['keyboard'], 'ch')
        self.assertEqual(self.shared['keyboard_variant'], selected_variant)

    def test_unconfirmed_language_still_supplies_default_layout(self):
        shared = {'lang': 'en_US', 'keyboard': 'us', 'keyboard_variant': ''}
        welcome = pages.build_welcome_page(shared, Adw.NavigationView())
        languages = next(w for w in descendants(welcome) if isinstance(w, Gtk.ListView))
        languages.get_model().set_selected(next(i for i, language in enumerate(LANGUAGES)
                                               if language.code == 'fr'))
        self.assertEqual((shared['keyboard'], shared['keyboard_variant']), ('fr', ''))

    def test_input_method_download_choices_are_only_on_software_page(self):
        self.build(lang='zh_CN', locale='zh_CN.UTF-8')
        self.assertFalse(any(isinstance(w, Gtk.CheckButton) for w in descendants(self.page)))
        software = pages.build_software_page(self.shared, self.nav)
        choices = [w.get_label() for w in descendants(software) if isinstance(w, Gtk.CheckButton)]
        self.assertTrue(any('Rime' in label for label in choices))


# The package GUI profile explicitly enables this inside its disposable Xvfb.
# Never change a developer's real desktop/Xwayland keymap during test discovery.
@unittest.skipUnless(os.environ.get('ANDUINOS_KEYBOARD_X11_TEST') == '1',
                     'requires the package GUI profile disposable Xvfb')
@unittest.skipUnless(ctypes.util.find_library('Xtst'),
                     'native X11 key injection requires libxtst6')
class NativeKeyboardInputTests(unittest.TestCase):
    def test_swiss_dollar_works_in_tester_selection_replacement_and_password(self):
        import gi
        gi.require_version('GdkX11', '4.0')
        from gi.repository import GdkX11

        settings = Gio.Settings.new_with_backend(
            'org.gnome.desktop.input-sources', Gio.memory_settings_backend_new())

        def apply(layout, variant=''):
            signature = apply_keyboard_layout(layout, variant, settings=settings)
            # Xvfb has no GNOME Shell. Simulate its compositor-side keymap
            # update, then exercise normal GDK/GTK event handling end to end.
            subprocess.run(['setxkbmap', '-model', 'pc105', '-layout', layout,
                            '-variant', variant, '-option', ''], check=True)
            return signature

        window = Adw.Window(default_width=960, default_height=740)
        self.addCleanup(window.destroy)
        self.addCleanup(lambda: subprocess.run(['setxkbmap', '-layout', 'us', '-variant', '', '-option', ''], check=True))
        nav = Adw.NavigationView()
        shared = {'lang': 'en_US', 'keyboard': 'ch', 'keyboard_variant': '',
                  '_page_route_initialized': True, '_network_page_planned': False}
        with patch('pages.apply_keyboard_layout', side_effect=apply):
            page = pages.build_keyboard_page(shared, nav)
            nav.add(page)
            window.set_content(nav)
            window.present()
            settle(0.4)
        entry = next(w for w in descendants(page) if isinstance(w, Gtk.Entry))
        self.assertTrue(entry.get_sensitive(), 'compositor keymap verification failed')
        self.assertEqual(settings.get_value('sources').unpack(), [('xkb', 'ch')])

        # Inject a physical X11 key, not text or Gtk.Entry.set_text.
        # No custom installer key controller may translate or swallow it.
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.argtypes = (ctypes.c_char_p,)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XFlush.argtypes = (ctypes.c_void_p,)
        x11.XSetInputFocus.argtypes = (ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        x11.XCloseDisplay.argtypes = (ctypes.c_void_p,)
        display = x11.XOpenDisplay(None)
        self.assertTrue(display)
        self.addCleanup(x11.XCloseDisplay, display)
        xid = GdkX11.X11Surface.get_xid(window.get_surface())
        x11.XSetInputFocus(display, xid, 1, 0)
        x11.XFlush(display)
        xtst = ctypes.CDLL(ctypes.util.find_library('Xtst'))
        xtst.XTestFakeKeyEvent.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_ulong)

        def dollar():
            for pressed in (True, False):
                self.assertTrue(xtst.XTestFakeKeyEvent(display, 51, pressed, 0))
            x11.XFlush(display)
            settle()

        entry.grab_focus()
        settle()
        dollar()
        self.assertEqual(entry.get_text(), '$')
        entry.select_region(0, -1)
        dollar()
        self.assertEqual(entry.get_text(), '$')
        password = Gtk.PasswordEntry()
        window.set_content(password)
        password.grab_focus()
        settle()
        dollar()
        self.assertEqual(password.get_text(), '$')
