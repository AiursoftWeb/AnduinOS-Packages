"""APT is an external boundary; these tests never touch the host package state."""
from contextlib import nullcontext
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch

import apt_pkg
from anduinos_driver_center import driver_update as update
from anduinos_driver_center.core import nvidia_restart_required


def package(name, old='1.0', new='2.0', remove=False):
    return Obj(name=name, fullname=name + ':amd64',
               installed=Obj(version=old) if old else None,
               candidate=Obj(version=new, origins=[Obj(trusted=True)]),
               essential=False, marked_delete=remove,
               _pkg=Obj(selected_state=apt_pkg.SELSTATE_INSTALL),
               mark_upgrade=lambda **kw: None)


class Cache(dict):
    broken_count = 0
    committed = False
    complete = True
    def clear(self): pass
    def get_changes(self): return list(self.values())
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def open(self): pass
    def commit(self, **kwargs):
        self.committed = True
        if self.complete:
            for p in self.values():
                p.installed = None if p.marked_delete else p.candidate
        return True
    def __contains__(self, key): return super().__contains__(key.split(':')[0])
    def __getitem__(self, key): return super().__getitem__(key.split(':')[0])


class UpdateTests(unittest.TestCase):
    def fixture(self):
        return Cache({p.name: p for p in [package('nvidia-driver-595-open'),
            package('linux-modules-nvidia-595-open-old-kernel', remove=True)]})

    def test_old_modules_can_be_removed_but_kernel_and_desktop_cannot(self):
        cache = self.fixture()
        proposal = update.plan(cache, 'nvidia-driver-595-open', 'current-kernel')
        self.assertTrue(any(c['after'] is None for c in proposal['changes']))
        for name in ('linux-image-old-kernel', 'anduinos-desktop',
                     'linux-modules-nvidia-595-open-current-kernel'):
            with self.subTest(name=name):
                altered = self.fixture()
                altered[name] = package(name, remove=True)
                with self.assertRaises(RuntimeError):
                    update.plan(altered, 'nvidia-driver-595-open', 'current-kernel')
                self.assertFalse(altered.committed)

    def test_changed_plan_never_commits(self):
        cache = self.fixture()
        approved = update.plan(cache, 'nvidia-driver-595-open', 'current-kernel')
        cache['nvidia-driver-595-open'].candidate.version = '3.0'
        with patch.object(update.apt, 'Cache', return_value=cache), \
             patch.object(update.apt_pkg, 'SystemLock', return_value=nullcontext()):
            with self.assertRaises(RuntimeError):
                update.execute('nvidia-driver-595-open', approved)
        self.assertFalse(cache.committed)

    def test_commit_success_is_not_enough_without_actual_package_changes(self):
        for complete in (True, False):
            with self.subTest(complete=complete):
                cache = self.fixture()
                cache.complete = complete
                approved = update.plan(cache, 'nvidia-driver-595-open', 'current-kernel')
                with patch.object(update.apt, 'Cache', return_value=cache), \
                     patch.object(update.apt_pkg, 'SystemLock', return_value=nullcontext()), \
                     patch.dict(update.os.environ, {}):
                    if complete:
                        update.execute('nvidia-driver-595-open', approved)
                        self.assertIsNone(cache['linux-modules-nvidia-595-open-old-kernel'].installed)
                    else:
                        with self.assertRaises(RuntimeError):
                            update.execute('nvidia-driver-595-open', approved)

    def test_hold_and_downgrade_are_not_silently_overridden(self):
        for held in (True, False):
            cache = self.fixture()
            item = package('libnvidia-gl-595', old='3.0', new='2.0')
            if held:
                item._pkg.selected_state = apt_pkg.SELSTATE_HOLD
            cache[item.name] = item
            with self.assertRaises(RuntimeError):
                update.plan(cache, 'nvidia-driver-595-open', 'current-kernel')

    def test_reboot_state_compares_loaded_and_installed_module_versions(self):
        for loaded, installed, expected in [('1.0', '2.0', True), ('2.0', '2.0', False), ('', '2.0', False)]:
            runner = Obj(run=lambda command: Obj(returncode=0, stdout=loaded))
            self.assertEqual(nvidia_restart_required('nvidia', installed, runner), expected)
