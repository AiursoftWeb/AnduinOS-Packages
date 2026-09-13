"""Render an upgradable driver and open its confirmation, without installation."""
import unittest
import json
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
from anduinos_driver_center.app import DriverCenterWindow
from anduinos_driver_center.core import DriverOption, HardwareDevice, SecureBootState


class DriverUpdatePageTests(unittest.TestCase):
    def test_upgradable_driver_page_and_confirmation_render(self):
        Gtk.init()
        Adw.init()
        app = Adw.Application(application_id='com.anduinos.DriverUpdateTest')
        app.register(None)
        with patch.object(DriverCenterWindow, 'refresh'):
            window = DriverCenterWindow(app)
        option = DriverOption('nvidia-driver-595-open', 'fixture', installed=True,
                              active=True, installed_version='1.0',
                              candidate_version='2.0', update_available=True)
        device = HardwareDevice('fixture', 'NVIDIA', 'Test GPU', options=(option,))
        try:
            page = window._graphics_page(device, SecureBootState(False, False, False, False, None))
            self.assertIsInstance(page, Gtk.Widget)
            with patch.object(window, '_run_action') as install:
                transaction = {
                    'package': option.package, 'before': '1.0', 'after': '2.0',
                    'changes': [{'package': 'linux-modules-nvidia-old', 'before': '1.0', 'after': None}],
                }
                window._update_preview_done(Gtk.Button(), transaction, None)
                dialog = next(w for w in Gtk.Window.list_toplevels()
                              if isinstance(w, Adw.MessageDialog) and w.get_transient_for() == window)
                self.assertIn(option.installed_version, dialog.get_body())
                self.assertIn(option.candidate_version, dialog.get_body())
                dialog.response('cancel')
                install.assert_not_called()
                window._update_preview_done(Gtk.Button(), transaction, None)
                dialog = next(w for w in Gtk.Window.list_toplevels()
                              if isinstance(w, Adw.MessageDialog) and w.get_transient_for() == window)
                dialog.response('update')
                self.assertEqual(json.loads(install.call_args.kwargs['stdin']), transaction)
        finally:
            window.destroy()
