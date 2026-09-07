"""Behavior tests run under a desktop session or xvfb-run."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gdk, GLib, Gtk
    from anduinos_driver_center import computer_ui as ui
    from anduinos_driver_center.computer import Computer, Disk, SystemDetails, FilesystemUsage
    Adw.init()
    HAS_DISPLAY = Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, 'GTK4/libadwaita and a display are required')
class ComputerPageTests(unittest.TestCase):
    def setUp(self):
        with patch.object(ui.ComputerPage, 'reload'):
            self.page = ui.ComputerPage()
        self.page.info = Computer(system='AnduinOS Test', anduinos=True, model='Test PC',
            cpu={'Model name': 'Test Processor', 'Core(s) per socket': '24', 'Socket(s)': '1', 'CPU(s)': '32'},
            disks=[Disk('sda', 'System SSD', 512_000_000_000, 'sata', False, True),
                   Disk('sdb', 'Backup HDD', 16_000_000_000_000, 'sata', True, False)],
            memory=32 * 1024**3, board='Test Board')
        self.page.render()

    def widgets(self, widget=None):
        widget = widget or self.page
        yield widget
        child = widget.get_first_child()
        while child:
            yield from self.widgets(child)
            child = child.get_next_sibling()

    def labels(self):
        return '\n'.join(widget.get_label() for widget in self.widgets() if isinstance(widget, Gtk.Label))

    def test_simple_expand_collapse_never_authenticates(self):
        with patch.object(ui.Gio.Subprocess, 'new') as start:
            self.assertIn('System SSD', self.labels())
            self.assertNotIn('Backup HDD', self.labels())
            self.assertIn('24 cores', self.labels())
            self.assertIn('32 threads', self.labels())
            self.page._toggle(None)
            self.assertIn('Backup HDD', self.labels())
            self.page._toggle(None)
            self.assertNotIn('Backup HDD', self.labels())
            start.assert_not_called()

    def test_logo_is_only_added_for_anduinos(self):
        self.assertEqual(sum(isinstance(w, Gtk.Picture) for w in self.widgets()), 1)
        self.page.info.anduinos = False
        self.page.render()
        self.assertEqual(sum(isinstance(w, Gtk.Picture) for w in self.widgets()), 0)

    def test_cancelled_auth_is_attempted_once_even_after_reload(self):
        self.page._toggle(None)
        with patch.object(ui.Gio.Subprocess, 'new', side_effect=GLib.Error('cancelled')) as start:
            self.page._read_memory(None)
            self.assertFalse(self.page.memory_pending)
            self.assertIn('Other hardware details remain available.', self.labels())
            self.assertIn('Backup HDD', self.labels())
            self.page._toggle(None)
            self.page._toggle(None)
            self.page._loaded(self.page._scan_generation, self.page.info)
            self.page._read_memory(None)
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0], ['pkexec', ui.MEMORY_HELPER])

    def test_successful_memory_read_and_pending_toggle(self):
        process = Mock()
        process.communicate_utf8_finish.return_value = (True, '[{"Size":"16 GB", "Type":"DDR5", "Configured Memory Speed":"4800 MT/s"}]', '')
        process.get_successful.return_value = True
        with patch.object(ui.Gio.Subprocess, 'new', return_value=process) as start:
            self.page._toggle(None)
            self.page._read_memory(None)
            self.assertTrue(self.page.memory_pending)
            self.page._toggle(None)
            self.page._memory_done(process, object())
            self.assertNotIn('DDR5', self.labels())
            self.page._toggle(None)
            self.assertIn('DDR5', self.labels())
            self.assertIn('4800 MT/s', self.labels())
            self.page._read_memory(None)
            start.assert_called_once()

    def test_details_show_system_packages_usage_and_frequency_only_when_expanded(self):
        self.page.info.cpu['CPU max MHz'] = '6000.0000'
        self.page.info.details = SystemDetails(
            desktop='GNOME 50.1', window_manager='Mutter', session_type='wayland',
            dpkg_count=2485, flatpak_count=55, uptime=15240,
            memory_used=10 * 1024**3, memory_total=32 * 1024**3, swap_total=0,
            appearance={'gtk-theme': 'Example Theme', 'cursor-theme': 'Example Cursor', 'cursor-size': '32'},
            filesystems=[FilesystemUsage('/dev/sda1', '/', 'ext4', 100 * 1024**3, 60 * 1024**3, 35 * 1024**3)])
        self.page.render()
        self.assertNotIn('2,485', self.labels())
        with patch.object(ui.Gio.Subprocess, 'new') as auth:
            self.page._toggle(None)
            labels = self.labels()
            for expected in ('6.00 GHz', 'GNOME 50.1', 'Mutter', 'Wayland', '2,485', '55',
                             '4 h 14 min', '10 GiB', '32 GiB', 'Available: 35 GiB',
                             'Not configured', 'Example Theme', 'Example Cursor'):
                self.assertIn(expected, labels)
            self.assertNotIn('CPU max MHz', labels)
            self.page._toggle(None)
            self.assertNotIn('2,485', self.labels())
            auth.assert_not_called()

    def test_zero_packages_and_failed_package_probe_are_distinct(self):
        self.page.info.details = SystemDetails(dpkg_count=0, flatpak_count=None)
        self.page._toggle(None)
        self.assertIn('0', self.labels())
        self.assertIn('Not available', self.labels())

    def test_display_connection_details_and_fractional_scale(self):
        monitor = Mock()
        monitor.get_connector.return_value = 'HDMI-A-1'
        monitor.get_model.return_value = 'External panel'
        monitor.get_width_mm.return_value = 600
        monitor.get_height_mm.return_value = 340
        monitor.get_geometry.return_value = Mock(width=2048, height=1152)
        monitor.get_scale.return_value = 1.25
        monitor.get_refresh_rate.return_value = 144000
        display = Mock()
        display.get_monitors.return_value.get_n_items.return_value = 1
        display.get_monitors.return_value.get_item.return_value = monitor
        with patch.object(self.page, 'get_display', return_value=display):
            result = self.page._displays()[0]
            self.assertIn('2560 × 1440', result[1])
            self.assertIn('External display', result[2])
            self.assertIn('HDMI-A-1', result[2])
            monitor.get_connector.return_value = 'eDP-1'
            self.assertIn('Built-in display', self.page._displays()[0][2])

    def test_stale_scan_cannot_replace_newer_inventory(self):
        self.page._scan_generation = 2
        self.page._loaded(1, Computer(system='Old scan'))
        self.assertEqual(self.page.info.system, 'AnduinOS Test')


if __name__ == '__main__':
    unittest.main()
