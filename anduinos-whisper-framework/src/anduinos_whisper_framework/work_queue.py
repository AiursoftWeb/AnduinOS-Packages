"""Bounded final work and a single replaceable preview, without PCM backlog."""
from collections import deque
import threading


class RecognitionQueue:
    def __init__(self, max_finals=8):
        self._ready = threading.Condition()
        self._finals = deque()
        self._partial = None
        self._quit = None
        self.max_finals = max_finals

    def put(self, work):
        with self._ready:
            priority = work[0]
            if priority < 0:
                self._quit = work
            elif priority == 0:
                if len(self._finals) >= self.max_finals:
                    return False
                self._partial = None
                self._finals.append(work)
            else:
                # Do not spend more memory/compute on previews while finals wait.
                if self._finals:
                    return False
                self._partial = work
            self._ready.notify()
            return True

    def get(self):
        with self._ready:
            self._ready.wait_for(lambda: self._quit or self._finals or self._partial)
            if self._quit:
                return self._quit
            if self._finals:
                return self._finals.popleft()
            work, self._partial = self._partial, None
            return work

    def clear(self, partial_only=False):
        with self._ready:
            self._partial = None
            if not partial_only:
                self._finals.clear()
