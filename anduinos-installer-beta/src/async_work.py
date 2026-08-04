"""Small main-loop bridge for cancellable background work."""

from __future__ import annotations

import threading


class LatestBackgroundRequest:
    """Run blocking work off-thread and deliver only the newest result."""

    def __init__(self, schedule, thread_factory=None):
        self._schedule = schedule
        self._thread_factory = thread_factory or threading.Thread
        self._request_id = 0
        self._active = True

    def activate(self):
        self._active = True

    def invalidate(self):
        self._active = False
        self._request_id += 1

    def start(self, work, on_complete):
        self._active = True
        self._request_id += 1
        request_id = self._request_id

        def _deliver(result, error):
            if self._active and request_id == self._request_id:
                on_complete(result, error)
            return False

        def _worker():
            try:
                result = work()
                error = None
            except Exception as exception:
                result = None
                error = exception
            self._schedule(_deliver, result, error)

        self._thread_factory(target=_worker, daemon=True).start()
        return request_id


class ProgressPulse:
    """Own one indeterminate progress timeout and remove it reliably."""

    def __init__(self, progress, timeout_add, source_remove):
        self._progress = progress
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        self._source_id = 0

    def start(self):
        self.stop()
        self._progress.set_visible(True)
        self._source_id = self._timeout_add(100, self._pulse)

    def _pulse(self):
        self._progress.pulse()
        return True

    def stop(self):
        if self._source_id:
            self._source_remove(self._source_id)
            self._source_id = 0
        self._progress.set_visible(False)
