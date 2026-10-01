import time
import unittest
from unittest.mock import Mock, patch
import pages
from gi.repository import Adw, GLib, Gtk
from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.probe import PlatformProbe, ProbeError
from ui import load_visual_style


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


class FirmwarePageTests(unittest.TestCase):
    def build(self):
        self.shared = {'_page_route_initialized': True}
        self.nav = Mock()
        self.request = Mock()
        with patch('pages.LatestBackgroundRequest', return_value=self.request):
            self.page = pages.build_firmware_check_page(self.shared, self.nav)
        buttons = [w for w in descendants(self.page) if isinstance(w, Gtk.Button)]
        self.next = next(w for w in buttons if w.get_label() == 'Continue')
        self.retry = next(w for w in buttons if w.get_label() == 'Retry')
        self.page.emit('shown')
        self.work, self.complete = self.request.start.call_args.args

    def test_probe_is_deferred_and_requests_recovery(self):
        with patch('pages.probe_platform') as probe:
            self.build()
            probe.assert_not_called()
            self.assertFalse(self.next.get_sensitive())
            self.work()
            probe.assert_called_once_with(recover=True)

    def test_unsupported_can_continue_without_recommendation(self):
        self.build()
        result = PlatformProbe(Architecture.AMD64, Firmware.UEFI, SecureBoot.UNSUPPORTED)
        self.complete(result, None)
        self.assertTrue(self.next.get_sensitive())
        self.assertFalse(self.retry.get_visible())
        labels = [w.get_label() for w in descendants(self.page)
                  if isinstance(w, Gtk.Label)]
        self.assertTrue(any('amd64 / uefi / Secure Boot: unsupported' in label
                            for label in labels))
        with (patch('pages._build_network_or_keyboard_page', return_value='network'),
              patch('pages.build_secure_boot_page') as recommendation):
            self.next.emit('clicked')
        recommendation.assert_not_called()
        self.nav.push.assert_called_once_with('network')
        self.assertEqual(self.shared['_platform_probe_result'], result)

    def test_failure_allows_configuration_without_inventing_platform(self):
        self.build()
        self.complete(None, ProbeError('firmware I/O error'))
        self.assertIsNone(self.shared['_platform_probe_result'])
        self.assertIn('firmware I/O error', self.shared['_platform_probe_error'])
        self.assertTrue(self.retry.get_visible())
        with patch('pages._build_network_or_keyboard_page', return_value='configuration'):
            self.next.emit('clicked')
        self.nav.push.assert_called_once_with('configuration')
        self.retry.emit('clicked')
        self.assertFalse(self.next.get_sensitive())
        self.assertEqual(self.request.start.call_count, 2)

    def test_bios_result_is_shown_without_secure_boot_recommendation(self):
        self.build()
        result = PlatformProbe(Architecture.AMD64, Firmware.BIOS,
                               SecureBoot.NOT_APPLICABLE)
        self.complete(result, None)
        labels = [w.get_label() for w in descendants(self.page)
                  if isinstance(w, Gtk.Label)]
        self.assertTrue(any('amd64 / bios / Secure Boot: not-applicable' in label
                            for label in labels))

    def test_hidden_page_invalidates_pending_delivery(self):
        self.build()
        self.page.emit('hidden')
        self.request.invalidate.assert_called_once_with()

    def test_result_card_and_navigation_fit_normal_and_small_windows(self):
        for width, height in ((960, 740), (800, 600)):
            with self.subTest(size=(width, height)):
                self.build()
                window = Adw.Window(default_width=width, default_height=height)
                try:
                    load_visual_style(window.get_display())
                    toolbar = Adw.ToolbarView()
                    toolbar.add_top_bar(Adw.HeaderBar())
                    navigation = Adw.NavigationView()
                    navigation.add(self.page)
                    toolbar.set_content(navigation)
                    window.set_content(toolbar)
                    window.present()
                    self.complete(PlatformProbe(Architecture.AMD64, Firmware.UEFI,
                                                SecureBoot.ENABLED), None)
                    self.settle()

                    widgets = list(descendants(self.page))
                    hero = next(w for w in widgets
                                if w.has_css_class('installer-hero'))
                    result = next(w for w in widgets if isinstance(w, Gtk.Label)
                                  and 'Secure Boot: enabled' in w.get_label())
                    heading = next(w for w in widgets if isinstance(w, Gtk.Label)
                                   and w.has_css_class('heading'))
                    scroll = next(w for w in widgets
                                  if isinstance(w, Gtk.ScrolledWindow))
                    self.assertEqual(hero.get_visible(), height > 620)
                    self.assertEqual(hero._title_label.get_label(),
                                     'Firmware check complete')
                    self.assertTrue(any(w.has_css_class('installer-card')
                                        and w.get_mapped() for w in widgets))
                    self.assertFalse(next(w for w in widgets
                                          if isinstance(w, Gtk.Spinner)).get_visible())
                    self.assertLess(result.get_height(), 80)
                    _, heading_bounds = heading.compute_bounds(window)
                    _, result_bounds = result.compute_bounds(window)
                    self.assertLess(result_bounds.get_y() - heading_bounds.get_y(), 80)
                    for widget in (heading, result, self.next):
                        ok, bounds = widget.compute_bounds(window)
                        self.assertTrue(ok)
                        self.assertTrue(widget.get_mapped())
                        self.assertGreaterEqual(bounds.get_y(), 0)
                        self.assertLessEqual(bounds.get_y() + bounds.get_height(),
                                             window.get_height())
                    if height == 600:
                        self.assertTrue(scroll.get_vscrollbar().get_mapped())

                    self.complete(None, ProbeError('firmware I/O error\n' * 80))
                    self.settle()
                    adjustment = scroll.get_vadjustment()
                    self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
                    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                    self.settle()
                    _, retry_bounds = self.retry.compute_bounds(scroll)
                    self.assertTrue(self.retry.get_mapped())
                    self.assertGreaterEqual(retry_bounds.get_y(), 0)
                    self.assertLessEqual(retry_bounds.get_y() + retry_bounds.get_height(),
                                         scroll.get_height())
                    _, next_bounds = self.next.compute_bounds(window)
                    self.assertLessEqual(next_bounds.get_y() + next_bounds.get_height(),
                                         window.get_height())
                finally:
                    window.destroy()

    @staticmethod
    def settle():
        deadline = time.monotonic() + 0.35
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.005)
