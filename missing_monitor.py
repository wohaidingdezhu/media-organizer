"""Bounded, read-only background checks of the current scan's source paths."""
import threading
import time
from media_actions import media_id


class MissingMonitor:
    def __init__(self, media, *, interval=300.0, delay=0.02):
        self.media = media
        self.interval = interval
        self.delay = delay
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.paths = set()
        self.revision = 0
        self.running = False
        self.checked = 0
        self.total = len(media.records)
        self.completed_at = 0.0
        self.priority = set()
        self.manual_epochs = {}

    def _set(self, path, missing, epoch=None):
        with self.lock:
            if epoch is not None and self.manual_epochs.get(path, 0) != epoch:
                return
            old = path in self.paths
            if missing:
                self.paths.add(path)
            else:
                self.paths.discard(path)
            if old != missing:
                self.revision += 1

    def mark_missing(self, paths):
        for path in paths:
            with self.lock:
                self.manual_epochs[path] = self.manual_epochs.get(path, 0) + 1
                if path not in self.paths:
                    self.paths.add(path)
                    self.revision += 1

    def snapshot(self):
        with self.lock:
            return set(self.paths), self._status()

    def _status(self):
        return {'revision': self.revision, 'missing_count': len(self.paths),
                'scanning': self.running, 'checked': self.checked, 'total': self.total}

    def status(self):
        with self.lock:
            return self._status()

    def request_scan(self, *, force=False):
        with self.lock:
            if (self.stopped.is_set() or self.running or not self.media.records or
                    not force and self.completed_at and time.monotonic() - self.completed_at < self.interval):
                return False
            self.running = True
            self.checked = 0
        threading.Thread(target=self._scan, args=(False,), name='media-missing-check', daemon=True).start()
        return True

    def recheck_missing(self):
        """Prioritize only hidden records without waiting for the full sweep."""
        with self.lock:
            if self.stopped.is_set():
                return 0
            self.priority.update(self.paths)
            count = len(self.priority)
            if not count or self.running:
                return count
            self.running = True
        threading.Thread(target=self._scan, args=(True,), name='media-missing-check', daemon=True).start()
        return count

    def _scan(self, only_priority=False):
        records = iter(()) if only_priority else iter(self.media.records.items())
        finished = False
        try:
            while not self.stopped.is_set():
                with self.lock:
                    if self.priority:
                        path = self.priority.pop()
                        identifier = media_id(path)
                        record = self.media.records.get(identifier)
                        priority = True
                    else:
                        try:
                            identifier, record = next(records)
                        except StopIteration:
                            # Decide under the same lock used to queue work, so
                            # a late refresh cannot be lost while exiting.
                            self.running = False
                            if not only_priority:
                                self.completed_at = time.monotonic()
                            finished = True
                            return
                        path = record['path']
                        priority = False
                    epoch = self.manual_epochs.get(path, 0)
                if record is None:
                    continue
                try:
                    state = self.media.availability(identifier)
                except (OSError, ValueError):
                    state = 'unavailable'
                self._set(path, state == 'missing', epoch)
                if not priority:
                    with self.lock:
                        self.checked += 1
                if self.stopped.wait(self.delay):
                    break
        finally:
            if not finished:
                with self.lock:
                    self.running = False
                    if not only_priority:
                        self.completed_at = time.monotonic()

    def stop(self):
        self.stopped.set()
