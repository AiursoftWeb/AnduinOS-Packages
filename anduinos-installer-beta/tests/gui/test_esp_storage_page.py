"""Real GTK manual-page smoke test; all device probes are replaced."""

import time
import unittest
from unittest.mock import Mock, patch

import pages
from gi.repository import Adw, GLib, Gtk
from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.model import EspReplacement
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import StorageInventory
from installer_core.storage_ui import build_storage_workflow
from test_frontend import state
from test_manual_layout import manual_disk, selection


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


def dispatch_refresh():
    deadline = time.monotonic() + 0.35
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


class EspStoragePageTests(unittest.TestCase):
    def test_real_warning_defaults_to_back_and_requires_destructive_response(self):
        parent = Gtk.Window()
        self.addCleanup(parent.destroy)
        parent.present()
        nav = Mock()
        nav.get_root.return_value = parent
        for response in ('back', 'replace'):
            with self.subTest(response=response):
                accepted, declined = Mock(), Mock()
                dialog = pages._esp_conflict_dialog(
                    nav, 'en_US', '/dev/vda1', accepted, declined)
                self.addCleanup(dialog.destroy)
                self.assertEqual(dialog.get_default_response(), 'back')
                self.assertEqual(dialog.get_close_response(), 'back')
                self.assertEqual(dialog.get_response_appearance('replace'),
                                 Adw.ResponseAppearance.DESTRUCTIVE)
                self.assertIn('/dev/vda1', dialog.get_body())
                self.assertIn('will not be formatted', dialog.get_body())
                self.assertIn('may no longer start', dialog.get_body())
                accepted.assert_not_called()
                declined.assert_not_called()
                dialog.response(response)
                (accepted if response == 'replace' else declined).assert_called_once_with()
                (declined if response == 'replace' else accepted).assert_not_called()
                dialog.destroy()

    def test_conflict_requires_new_esp_and_preserves_root_geometry(self):
        disk = manual_disk()
        draft = selection()
        shared = state()
        shared.update(disk_stable_id=disk.identity.stable_id,
                      disk_topology_digest=disk.topology_digest,
                      disk_size_bytes=disk.identity.expected_size_bytes,
                      manual_storage_selection_model=draft)
        workflow = build_storage_workflow(
            StorageInventory((disk,), "e" * 64),
            PlatformProbe(Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED),
            physical_memory_probe=lambda: 8 * 1024**3,
        )
        request = Mock()
        nav = Mock()
        with patch("pages.LatestBackgroundRequest", return_value=request), \
             patch("pages.bind_storage_target", return_value=False), \
             patch("pages._confirm_storage_capacity", side_effect=lambda p, n, l, s, done: done()), \
             patch("pages._esp_conflict_dialog") as dialog, \
             patch("pages.probe_esp_replacement", return_value=EspReplacement(
                 disk.partitions[0].identity.partuuid, disk.partitions[0].filesystem_uuid,
                 "a" * 64)) as probe:
            page = pages.build_advanced_storage_page(shared, nav)
            window = Gtk.Window()
            self.addCleanup(window.destroy)
            window.set_child(page)
            window.present()
            dispatch_refresh()
            request.start.call_args.args[1](workflow, None)
            dispatch_refresh()
            next_button = next(w for w in descendants(page)
                               if isinstance(w, Gtk.Button) and w.get_label() == "Next")
            self.assertTrue(next_button.get_sensitive(), "\n".join(
                w.get_text() for w in descendants(page) if isinstance(w, Gtk.Label)
            ))
            next_button.emit("clicked")
            self.assertFalse(next_button.get_sensitive())
            worker, complete = request.start.call_args.args
            complete(worker(), None)
            dispatch_refresh()
            probe.assert_called_once()
            dialog.assert_called_once()
            dialog.call_args.args[4]()
            dispatch_refresh()
            self.assertEqual(shared["manual_storage_selection_model"].reused_esp_partuuid, "")
            self.assertEqual(shared["manual_storage_selection_model"].new_partitions, draft.new_partitions)
            self.assertFalse(next_button.get_sensitive())
            nav.push.assert_not_called()
            window.destroy()


if __name__ == "__main__":
    unittest.main()
