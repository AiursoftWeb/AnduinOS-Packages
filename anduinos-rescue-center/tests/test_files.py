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

    def test_export_pins_destination_and_preserves_colliding_files(self):
        from contextlib import nullcontext
        from anduinos_rescue_center import files

        for replace_parent, collision, directory_source, unsafe_child in (
            (True, False, False, False), (False, True, False, False),
            (False, False, True, False), (False, False, True, True),
        ):
            with self.subTest(replace_parent=replace_parent, collision=collision, directory_source=directory_source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                home = root / 'home'
                destination = home / 'exports'
                destination.mkdir(parents=True)
                outside = root / 'outside'
                outside.mkdir()
                offline = root / 'offline'
                offline.mkdir()
                source = offline / 'report'
                if directory_source:
                    source.mkdir()
                    (source / 'document').write_text('rescued')
                    if unsafe_child:
                        (source / 'unsafe').symlink_to(outside)
                else:
                    source.write_text('rescued')
                if collision:
                    (destination / 'report').write_text('original')
                account = SimpleNamespace(pw_dir=str(home), pw_name='live', pw_uid=os.getuid(), pw_gid=os.getgid())
                original_copy = files._copy_safe
                moved = home / 'original-exports'

                def copy_with_parent_change(*args):
                    if replace_parent:
                        destination.rename(moved)
                        destination.symlink_to(outside, target_is_directory=True)
                    original_copy(*args)

                with patch.object(files, 'resolve_target', return_value=SimpleNamespace(path='/dev/fixture', filesystem='ext4')), patch.object(files, 'mounted_readonly', return_value=nullcontext(offline)), patch.object(files, 'inspect_mounted_filesystem', return_value=SimpleNamespace(os_kind='anduinos')), patch.object(files.pwd, 'getpwuid', return_value=account), patch.object(files, '_copy_safe', side_effect=copy_with_parent_change):
                    if unsafe_child:
                        with self.assertRaises(RuntimeError):
                            files.export_file('/dev/fixture', 'fixture', 'report', str(destination), caller_uid=1000)
                        self.assertFalse((destination / 'report').exists())
                    elif collision:
                        with self.assertRaises(FileExistsError):
                            files.export_file('/dev/fixture', 'fixture', 'report', str(destination), caller_uid=1000)
                        self.assertEqual((destination / 'report').read_text(), 'original')
                    else:
                        files.export_file('/dev/fixture', 'fixture', 'report', str(destination), caller_uid=1000)
                        actual = moved if replace_parent else destination
                        payload = actual / 'report'
                        self.assertEqual((payload / 'document' if directory_source else payload).read_text(), 'rescued')
                    self.assertEqual(list(outside.iterdir()), [])
                    actual = moved if replace_parent else destination
                    self.assertFalse(any(p.name.startswith('.anduinos-export-') for p in actual.iterdir()))

class OfflineSubvolumeBoundaryTests(unittest.TestCase):
    def test_root_and_home_symlinks_cannot_read_outside_offline_filesystem(self):
        for subvolume, relative in (('@root', 'secret.txt'), ('@home', 'home/secret.txt')):
            with self.subTest(subvolume=subvolume), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                top = base / 'offline'
                top.mkdir()
                outside = base / 'outside'
                outside.mkdir()
                (outside / 'secret.txt').write_text('fixture')
                (top / subvolume).symlink_to(outside, target_is_directory=True)
                with self.assertRaises(RuntimeError):
                    logical_path(top, 'btrfs', relative)
