"""Offline MokManager tutorial shared by the installer and trust panel."""
from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402


class EnrollmentTutorial(Gtk.AspectFrame):
    """Play the bundled recording while visible; restart it when shown again."""

    def __init__(self, instructions: str, *, scrollable: bool = False):
        super().__init__(ratio=1.6, obey_child=False)
        self._scrollable = scrollable
        self._picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
        self._picture.set_alternative_text(instructions)
        self.set_child(self._picture)
        self._animation = None
        self._iterator = None
        self._timeout = 0
        self.connect("map", self._start)
        self.connect("unmap", self._stop)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 640, -1, -1
        # Reserve the full aspect ratio, so a short installer page scrolls
        # instead of squeezing the recording into a thin strip.
        height = round((640 if for_size < 0 else for_size) / 1.6)
        return height if self._scrollable else 0, height, -1, -1

    def _start(self, _widget):
        try:
            self._animation = GdkPixbuf.PixbufAnimation.new_from_file(
                str(Path(__file__).with_name("mok-enrollment.gif")))
        except GLib.Error as error:
            # Text instructions and restart controls remain usable if the asset
            # is missing or unreadable; presentation never controls enrollment.
            logging.getLogger(__name__).warning("Could not load MOK tutorial: %s", error)
            self.set_visible(False)
            return
        self.set_ratio(self._animation.get_width() / self._animation.get_height())
        self._iterator = self._animation.get_iter(None)
        self._show_frame()
        self._schedule()

    def _show_frame(self):
        self._picture.set_paintable(Gdk.Texture.new_for_pixbuf(self._iterator.get_pixbuf()))

    def _schedule(self):
        delay = self._iterator.get_delay_time()
        if delay >= 0:
            self._timeout = GLib.timeout_add(max(20, delay), self._advance)

    def _advance(self):
        self._timeout = 0
        if self._iterator.advance(None):
            self._show_frame()
        self._schedule()
        return GLib.SOURCE_REMOVE

    def _stop(self, _widget):
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        self._iterator = None
        self._animation = None
