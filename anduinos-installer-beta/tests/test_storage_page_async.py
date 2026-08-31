import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from async_work import LatestBackgroundRequest, ProgressPulse
from pages import _probe_install_target


ROOT = Path(__file__).resolve().parents[1]


class ManualThread:
    def __init__(self, pending, *, target, daemon):
        self.pending = pending
        self.target = target
        self.daemon = daemon

    def start(self):
        self.pending.append(self.target)


class FakeProgress:
    def __init__(self):
        self.visible = False
        self.pulses = 0

    def set_visible(self, visible):
        self.visible = visible

    def pulse(self):
        self.pulses += 1


class StoragePageAsyncTests(unittest.TestCase):
    def test_development_recheck_keeps_the_synthetic_disk_inventory(self):
        workflow = SimpleNamespace(
            inventory=object(),
            platform=object(),
        )
        with (
            patch(
                "pages._probe_storage_workflow",
                return_value=workflow,
            ) as probe_workflow,
            patch("pages.probe_storage_inventory") as real_inventory,
            patch("pages.probe_platform") as real_platform,
        ):
            result = _probe_install_target(development_mode=True)

        self.assertEqual(result, (workflow.inventory, workflow.platform))
        probe_workflow.assert_called_once_with(development_mode=True)
        real_inventory.assert_not_called()
        real_platform.assert_not_called()

    def test_blocking_probe_starts_without_blocking_the_caller(self):
        entered = threading.Event()
        release = threading.Event()
        scheduled = []
        completed = []
        request = LatestBackgroundRequest(
            schedule=lambda callback, *args: scheduled.append(
                (callback, args)
            )
        )

        def blocking_probe():
            entered.set()
            release.wait(2)
            return "inventory"

        request.start(
            blocking_probe,
            lambda result, error: completed.append((result, error)),
        )
        self.assertTrue(entered.wait(1))
        self.assertEqual(completed, [])
        release.set()
        for _ in range(100):
            if scheduled:
                break
            threading.Event().wait(0.01)
        self.assertTrue(scheduled)
        self.assertEqual(completed, [])
        callback, args = scheduled.pop()
        callback(*args)
        self.assertEqual(completed, [("inventory", None)])

    def test_only_latest_request_updates_the_main_thread(self):
        pending = []
        scheduled = []
        completed = []
        request = LatestBackgroundRequest(
            schedule=lambda callback, *args: scheduled.append(
                (callback, args)
            ),
            thread_factory=lambda **kwargs: ManualThread(
                pending, **kwargs
            ),
        )
        request.start(
            lambda: "old",
            lambda result, error: completed.append((result, error)),
        )
        request.start(
            lambda: "new",
            lambda result, error: completed.append((result, error)),
        )
        pending[1]()
        pending[0]()
        for callback, args in scheduled:
            callback(*args)
        self.assertEqual(completed, [("new", None)])

    def test_failure_and_invalidation_are_delivered_safely(self):
        pending = []
        scheduled = []
        completed = []
        request = LatestBackgroundRequest(
            schedule=lambda callback, *args: scheduled.append(
                (callback, args)
            ),
            thread_factory=lambda **kwargs: ManualThread(
                pending, **kwargs
            ),
        )

        def fail():
            raise RuntimeError("probe failed")

        request.start(
            fail,
            lambda result, error: completed.append((result, error)),
        )
        pending.pop()()
        callback, args = scheduled.pop()
        callback(*args)
        self.assertIsNone(completed[0][0])
        self.assertRegex(str(completed[0][1]), "probe failed")

        request.start(
            lambda: "stale",
            lambda result, error: completed.append((result, error)),
        )
        pending.pop()()
        request.invalidate()
        callback, args = scheduled.pop()
        callback(*args)
        self.assertEqual(len(completed), 1)

    def test_indeterminate_progress_stops_and_removes_its_timer(self):
        progress = FakeProgress()
        timers = []
        removed = []
        pulse = ProgressPulse(
            progress,
            timeout_add=lambda interval, callback: (
                timers.append((interval, callback)) or 17
            ),
            source_remove=removed.append,
        )
        pulse.start()
        self.assertTrue(progress.visible)
        self.assertEqual(timers[0][0], 100)
        self.assertTrue(timers[0][1]())
        self.assertEqual(progress.pulses, 1)
        pulse.stop()
        self.assertFalse(progress.visible)
        self.assertEqual(removed, [17])

    def test_all_storage_waits_use_indeterminate_progress(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count('add_css_class("installer-progress")'), 4
        )
        self.assertIn('_("Loading storage devices…", lang)', source)
        self.assertIn('_("Rechecking target disk…", lang)', source)
        self.assertEqual(
            source.count("lambda: _probe_storage_workflow("),
            3,
        )
        self.assertIn("recheck_requests.start(_probe_target", source)
        self.assertIn(
            "_probe_install_target(\n"
            "                development_mode=development_mode",
            source,
        )
        self.assertIn('page.connect("unmap", _page_unmapped)', source)

    def test_disk_probe_starts_only_after_the_page_is_mapped(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        disk_page = source.split("def build_disk_page", 1)[1].split(
            "# ── page 5:", 1
        )[0]
        mapped_handler = disk_page.split("def _page_mapped", 1)[1].split(
            'page.connect("map", _page_mapped)', 1
        )[0]

        self.assertIn(
            "_populate_disks(restore_selection=True)", mapped_handler
        )
        after_connections = disk_page.split(
            'page.connect("unmap", _page_unmapped)', 1
        )[1]
        self.assertNotIn(
            "_populate_disks(restore_selection=True)", after_connections
        )

    def test_conditional_storage_pages_restart_probe_after_remapping(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        sections = (
            source.split("def build_guided_storage_page", 1)[1].split(
                "# ── page 6: Manual GPT storage", 1
            )[0],
            source.split("def build_advanced_storage_page", 1)[1].split(
                "def _find_live_device", 1
            )[0],
        )
        for page_source in sections:
            mapped_handler = page_source.split(
                "def _page_mapped", 1
            )[1].split("def _page_unmapped", 1)[0]
            self.assertIn("requests.activate()", mapped_handler)
            self.assertIn("_load_workflow()", mapped_handler)
            after_connections = page_source.split(
                'page.connect("unmap", _page_unmapped)', 1
            )[1]
            self.assertNotIn("_load_workflow()", after_connections)

    def test_ntfs_resize_request_is_owned_by_the_manual_page(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        disk_page = source.split("def build_disk_page", 1)[1].split(
            "def build_guided_storage_page", 1
        )[0]
        manual_page = source.split(
            "def build_advanced_storage_page", 1
        )[1].split("def _find_live_device", 1)[0]
        declaration = "resize_requests = LatestBackgroundRequest(GLib.idle_add)"
        self.assertNotIn(declaration, disk_page)
        self.assertEqual(manual_page.count(declaration), 1)
        self.assertIn("resize_requests.activate()", manual_page)
        self.assertIn("resize_requests.invalidate()", manual_page)
        self.assertIn("resize_requests.start(", manual_page)

    def test_manual_widget_rebuilds_are_deferred_out_of_signal_handlers(self):
        source = (ROOT / "src/pages.py").read_text(encoding="utf-8")
        advanced = source.split(
            "def build_advanced_storage_page", 1
        )[1].split("def _find_live_device", 1)[0]

        self.assertIn("def _queue_refresh():", advanced)
        self.assertIn(
            "refresh_source_id = GLib.timeout_add(250, run_refresh)",
            advanced,
        )
        self.assertGreaterEqual(advanced.count("_queue_refresh()"), 8)
        self.assertIn("retired_widgets.append(child)", advanced)
        self.assertIn("retired_widgets.clear()", advanced)
        # The only direct call belongs to the deferred callback. User signal
        # handlers must not synchronously tear down their own widget tree.
        self.assertEqual(
            sum(
                line.strip() == "_refresh()"
                for line in advanced.splitlines()
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
