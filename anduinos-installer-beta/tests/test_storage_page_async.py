import threading
import unittest
from pathlib import Path

from async_work import LatestBackgroundRequest, ProgressPulse


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
        self.assertIn("requests.start(_probe_storage_workflow", source)
        self.assertIn("recheck_requests.start(_probe_target", source)
        self.assertIn('page.connect("unmap", _page_unmapped)', source)


if __name__ == "__main__":
    unittest.main()
