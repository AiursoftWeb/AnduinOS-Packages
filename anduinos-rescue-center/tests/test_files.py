import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anduinos_rescue_center.files import (
    _copy_safe,
    allowed_destination,
    logical_path,
)


class LogicalPathTests(unittest.TestCase):
    def test_btrfs_home_is_mapped_to_independent_home_subvolume(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            (top / "@root/home").mkdir(parents=True)
            (top / "@home/alice/Documents").mkdir(parents=True)
            found, logical = logical_path(top, "btrfs", "home/alice/Documents")
            self.assertEqual(found, top / "@home/alice/Documents")
            self.assertEqual(logical, "home/alice/Documents")

    def test_rejects_parent_traversal_and_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            (top / "etc").mkdir()
            (top / "etc/escape").symlink_to("/etc")
            with self.assertRaises(ValueError):
                logical_path(top, "ext4", "../etc")
            with self.assertRaises(RuntimeError):
                logical_path(top, "ext4", "etc/escape")


class ExportTests(unittest.TestCase):
    def test_destination_is_limited_to_calling_users_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            target = home / "Exports"
            target.mkdir(parents=True)
            account = SimpleNamespace(pw_dir=str(home), pw_name="live", pw_uid=1000, pw_gid=1000)
            with patch("anduinos_rescue_center.files.pwd.getpwuid", return_value=account):
                self.assertEqual(allowed_destination(target, 1000), target)
                with self.assertRaises(RuntimeError):
                    allowed_destination(Path("/tmp"), 1000)

    def test_copy_rejects_symlinks_instead_of_following_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "escape").symlink_to("/etc/passwd")
            destination.mkdir()
            with self.assertRaises(RuntimeError):
                _copy_safe(source, destination / "copy", os.getuid(), os.getgid())
            self.assertFalse((destination / "copy/escape").exists())

    def test_copy_preserves_regular_file_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "report.txt"
            target = root / "copy.txt"
            source.write_text("rescued", encoding="utf-8")
            _copy_safe(source, target, os.getuid(), os.getgid())
            self.assertEqual(target.read_text(encoding="utf-8"), "rescued")
