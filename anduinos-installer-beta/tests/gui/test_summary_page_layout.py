"""Long disk identities must not push installation controls off screen."""

import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import pages
from gi.repository import Adw, GLib, Gtk

from ui import load_visual_style


def _descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _descendants(child)
        child = child.get_next_sibling()


def _settle():
    deadline = time.monotonic() + 0.35
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


class SummaryPageLayoutTests(unittest.TestCase):
    def test_missing_optional_partitions_require_confirmation_before_disk_recheck(self):
        from test_manual_layout import manual_disk, selection
        from installer_core.model import Architecture, Firmware, SecureBoot
        from installer_core.probe import PlatformProbe
        from installer_core.storage_inventory import StorageInventory
        from installer_core.storage_ui import build_manual_storage_preview, build_storage_workflow
        from test_frontend import state
        disk = manual_disk()
        platform = PlatformProbe(Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED)
        workflow = build_storage_workflow(StorageInventory((disk,), "e" * 64), platform,
                                          physical_memory_probe=lambda: 8 * 1024**3)
        base = selection()
        chosen = replace(base, new_partitions=base.new_partitions[:1])
        preview = build_manual_storage_preview(workflow, chosen)
        shared = state()
        shared.update(development_mode=True, storage_mode="manual", filesystem="btrfs",
                      disk=disk.identity.path, manual_storage_preview_model=preview,
                      _platform_probe_result=platform)
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        nav = Mock()
        nav.get_root.return_value = window
        request = Mock()
        with patch("pages.LatestBackgroundRequest", return_value=request):
            page = pages.build_summary_page(shared, nav)
            window.set_child(page)
            window.present()
            _settle()
            footer = next(w for w in _descendants(page) if w.has_css_class("wizard-navigation"))
            for response in ("back", "continue"):
                footer.next_button.emit("clicked")
                _settle()
                dialog = next(w for w in Gtk.Window.get_toplevels()
                              if isinstance(w, Adw.MessageDialog) and w.get_visible())
                self.addCleanup(dialog.destroy)
                self.assertEqual(dialog.get_heading(), "Review optional partitions")
                self.assertIn("Live USB", dialog.get_body())
                self.assertIn("ZRAM", dialog.get_body())
                self.assertEqual(dialog.get_default_response(), "back")
                self.assertEqual(dialog.get_close_response(), "back")
                request.start.assert_not_called()
                nav.push.assert_not_called()
                dialog.response(response)
                _settle()
                if response == "back":
                    self.assertTrue(footer.next_button.get_sensitive())
                    request.start.assert_not_called()
                else:
                    request.start.assert_called_once()
                dialog.destroy()

    def test_long_disk_identity_keeps_navigation_visible_and_summary_scrollable(self):
        stable_id = "wwn:nvme." + "144d5336374b4e453057313030333830" * 6
        for lang in ("en_US", "zh_CN"):
            for width, height in ((800, 600), (960, 720)):
                with self.subTest(lang=lang, size=(width, height)):
                    window = Adw.Window(default_width=width, default_height=height)
                    try:
                        load_visual_style(window.get_display())
                        toolbar = Adw.ToolbarView()
                        toolbar.add_top_bar(Adw.HeaderBar())
                        navigation = Adw.NavigationView()
                        page = pages.build_summary_page({
                            "lang": lang,
                            "disk": "/dev/nvme0n1",
                            "disk_size": "953.9 GiB",
                            "disk_model": "SAMSUNG MZAL41T0HBLB-00BL2",
                            "disk_stable_id": stable_id,
                        }, navigation)
                        navigation.add(page)
                        toolbar.set_content(navigation)
                        window.set_content(toolbar)
                        window.present()
                        _settle()

                        widgets = list(_descendants(page))
                        summary = next(w for w in widgets
                                       if isinstance(w, Gtk.Label)
                                       and stable_id in w.get_text())
                        footer = next(w for w in widgets
                                      if w.has_css_class("wizard-navigation"))
                        scroll = next(w for w in widgets
                                      if isinstance(w, Gtk.ScrolledWindow)
                                      and summary in _descendants(w))
                        adjustment = scroll.get_vadjustment()
                        self.assertGreater(adjustment.get_upper(),
                                           adjustment.get_page_size())
                        for position in (0, adjustment.get_upper()
                                         - adjustment.get_page_size()):
                            adjustment.set_value(position)
                            _settle()
                            for button in (footer.back_button, footer.next_button):
                                self.assertTrue(button.get_mapped())
                                ok, bounds = button.compute_bounds(window)
                                self.assertTrue(ok)
                                self.assertGreaterEqual(bounds.get_x(), 0)
                                self.assertGreaterEqual(bounds.get_y(), 0)
                                self.assertLessEqual(bounds.get_x() + bounds.get_width(),
                                                     window.get_width())
                                self.assertLessEqual(bounds.get_y() + bounds.get_height(),
                                                     window.get_height())
                    finally:
                        window.destroy()
