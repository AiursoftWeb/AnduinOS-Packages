import unittest
from unittest.mock import Mock, patch
import pages
from gi.repository import Gtk
from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.probe import PlatformProbe, ProbeError


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
        with patch('pages._build_network_or_keyboard_page', return_value='configuration'):
            self.next.emit('clicked')
        self.nav.push.assert_called_once_with('configuration')
        self.retry.emit('clicked')
        self.assertFalse(self.next.get_sensitive())
        self.assertEqual(self.request.start.call_count, 2)

    def test_hidden_page_invalidates_pending_delivery(self):
        self.build()
        self.page.emit('hidden')
        self.request.invalidate.assert_called_once_with()
