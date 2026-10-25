"""Exercise real GTK shrinking controls, with no device or Polkit access."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import pages
from gi.repository import Adw, Gtk
from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import StorageInventory
from installer_core.storage_ui import build_storage_workflow
from installer_core.ext4_resize import inspect_ext4_resize
from test_ext4_resize import readonly_run
from test_esp_storage_page import descendants, dispatch_refresh
from test_frontend import state
from test_manual_layout import manual_disk, selection


class Ext4StoragePageTests(unittest.TestCase):
    def test_ext4_requires_backup_ack_and_only_records_a_plan_then_can_undo(self):
        self.addCleanup(dispatch_refresh)
        disk = manual_disk()
        workflow = build_storage_workflow(StorageInventory((disk,), "e" * 64),
            PlatformProbe(Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED),
            physical_memory_probe=lambda: 8 * 1024**3)
        shared = state()
        shared.update(disk_stable_id=disk.identity.stable_id,
            disk_topology_digest=disk.topology_digest,
            disk_size_bytes=disk.identity.expected_size_bytes,
            manual_storage_selection_model=selection())
        requests, resize_requests = Mock(), Mock()
        window = Gtk.Window(default_width=1200, default_height=1000)
        self.addCleanup(window.destroy)
        nav = Mock()
        nav.get_root.return_value = window
        def current_dialog():
            instance = next(w for w in Gtk.Window.get_toplevels()
                            if isinstance(w, Adw.MessageDialog) and w.get_visible())
            self.addCleanup(instance.destroy)
            return instance
        partition = disk.partitions[1]
        inspection = inspect_ext4_resize(partition.identity.path,
            partition.identity.size_bytes, run=readonly_run)
        with patch("pages.LatestBackgroundRequest", side_effect=(requests, resize_requests)), \
                patch("pages.bind_storage_target", return_value=False), \
                patch("pages.probe_partition_resize", return_value=inspection) as probe:
            page = pages.build_advanced_storage_page(shared, nav)
            window.set_child(page)
            window.present()
            dispatch_refresh()
            requests.start.call_args.args[1](workflow, None)
            dispatch_refresh()
            self.assertEqual(sum(isinstance(w, Gtk.Button) and w.get_label() == "Change Size"
                                 for w in descendants(page)), 1)
            button = next(w for w in descendants(page)
                          if isinstance(w, Gtk.Button) and w.get_label() == "Change Size")
            button.emit("clicked")
            worker, complete = resize_requests.start.call_args.args
            complete(worker(), None)
            dispatch_refresh()
            probe.assert_called_once_with(partition.identity.path, filesystem="ext4",
                development_mode=False, current_size_bytes=partition.identity.size_bytes)
            popup = current_dialog()
            self.assertFalse(popup.get_response_enabled("apply"))
            self.assertEqual(shared["manual_storage_selection_model"].resized_partitions, ())
            form = popup.get_extra_child()
            labels = [w.get_text() for w in descendants(form) if isinstance(w, Gtk.Label)]
            self.assertTrue(any("estimate" in text for text in labels))
            self.assertFalse(any("Windows" in text for text in labels))
            next(w for w in descendants(form) if isinstance(w, Gtk.SpinButton)).set_value(40 * 1024)
            ack = next(w for w in descendants(form) if isinstance(w, Gtk.CheckButton))
            ack.set_active(True)
            self.assertTrue(popup.get_response_enabled("apply"))
            popup.response("apply")
            popup.destroy()
            dispatch_refresh()
            planned = shared["manual_storage_selection_model"].resized_partitions
            self.assertEqual(len(planned), 1)
            self.assertEqual(planned[0].target_size_mib, 40 * 1024)
            button = next(w for w in descendants(page)
                          if isinstance(w, Gtk.Button) and w.get_label() == "Change Size")
            button.emit("clicked")
            worker, complete = resize_requests.start.call_args.args
            complete(worker(), None)
            dispatch_refresh()
            popup = current_dialog()
            popup.response("remove")
            popup.destroy()
            dispatch_refresh()
            self.assertEqual(shared["manual_storage_selection_model"].resized_partitions, ())

    def test_btrfs_has_no_shrink_action_and_explains_why(self):
        self.addCleanup(dispatch_refresh)
        original = manual_disk()
        disk = replace(original, partitions=(original.partitions[0],
            replace(original.partitions[1], filesystem_type="btrfs")))
        workflow = build_storage_workflow(StorageInventory((disk,), "e" * 64),
            PlatformProbe(Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED),
            physical_memory_probe=lambda: 8 * 1024**3)
        request = Mock()
        shared = state()
        shared.update(disk_stable_id=disk.identity.stable_id,
            disk_size_bytes=disk.identity.expected_size_bytes,
            disk_topology_digest=disk.topology_digest,
            manual_storage_selection_model=selection())
        with patch("pages.LatestBackgroundRequest", return_value=request), \
                patch("pages.bind_storage_target", return_value=False):
            page = pages.build_advanced_storage_page(shared, Mock())
            window = Gtk.Window()
            self.addCleanup(window.destroy)
            window.set_child(page)
            window.present()
            dispatch_refresh()
            request.start.call_args.args[1](workflow, None)
            dispatch_refresh()
            self.assertFalse(any(isinstance(w, Gtk.Button) and w.get_label() == "Change Size"
                                 for w in descendants(page)))
            self.assertTrue(any(isinstance(w, Gtk.Label) and "Btrfs shrinking is not supported" in w.get_text()
                                for w in descendants(page)))
