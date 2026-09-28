import tempfile
import unittest
from pathlib import Path

from anduinos_rescue_center.live import (
    LIVE_MARKERS, is_live_environment, is_trusted_live_environment,
)


class LiveDetectionTests(unittest.TestCase):
    def test_explicit_runtime_marker_identifies_live_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "rootfs.squashfs"
            marker.touch()
            self.assertTrue(
                is_live_environment(markers=(marker,), cmdline_path=root / "missing")
            )

    def test_dracut_kernel_argument_identifies_live_session(self):
        with tempfile.TemporaryDirectory() as directory:
            cmdline = Path(directory) / "cmdline"
            cmdline.write_text("quiet splash rd.anduinos.live=1", encoding="utf-8")
            self.assertTrue(is_live_environment(markers=(), cmdline_path=cmdline))

    def test_normal_system_is_not_misclassified(self):
        with tempfile.TemporaryDirectory() as directory:
            cmdline = Path(directory) / "cmdline"
            cmdline.write_text("quiet splash root=UUID=abc", encoding="utf-8")
            self.assertFalse(is_live_environment(markers=(), cmdline_path=cmdline))

    def test_generic_cdrom_mountpoint_is_not_a_live_contract(self):
        self.assertNotIn(Path("/cdrom"), LIVE_MARKERS)

    def test_root_shell_requires_completed_dracut_runtime_not_just_a_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / "environment"
            source = root / "rootfs.squashfs"
            media = root / "cdrom"
            media.mkdir()
            environment.write_text("ANDUINOS_LIVE=1\n", encoding="utf-8")
            self.assertFalse(is_trusted_live_environment(
                environment=environment, source=source, media=media,
            ))
            source.write_bytes(b"squashfs")
            self.assertTrue(is_trusted_live_environment(
                environment=environment, source=source, media=media,
            ))
            environment.write_text("ANDUINOS_LIVE=0\n", encoding="utf-8")
            self.assertFalse(is_trusted_live_environment(
                environment=environment, source=source, media=media,
            ))
