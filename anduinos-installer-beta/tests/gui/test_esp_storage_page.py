"""Real GTK manual-page smoke test; all device probes are replaced."""

import time
import unittest
from unittest.mock import Mock, patch

import pages
from gi.repository import GLib, Gtk
from installer_core.model import Architecture, Firmware, SecureBoot
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
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


class EspStoragePageTests(unittest.TestCase):
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
             patch("pages.probe_esp_occupied", return_value=True) as probe:
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
            self.assertEqual(shared["manual_storage_selection_model"].reused_esp_partuuid, "")
            self.assertEqual(shared["manual_storage_selection_model"].new_partitions, draft.new_partitions)
            self.assertFalse(next_button.get_sensitive())
            nav.push.assert_not_called()
            window.destroy()


if __name__ == "__main__":
    unittest.main()
