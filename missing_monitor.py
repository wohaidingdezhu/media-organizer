"""Bounded, read-only background checks of the current scan's source paths."""
import threading
import time


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

    def _set(self, path, missing):
        with self.lock:
            old = path in self.paths
            if missing:
                self.paths.add(path)
            else:
                self.paths.discard(path)
            if old != missing:
                self.revision += 1

    def mark_missing(self, paths):
        for path in paths:
            self._set(path, True)

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
        threading.Thread(target=self._scan, name='media-missing-check', daemon=True).start()
        return True

    def _scan(self):
        try:
            for identifier, record in self.media.records.items():
                if self.stopped.is_set():
                    break
                try:
                    state = self.media.availability(identifier)
                except (OSError, ValueError):
                    state = 'unavailable'
                self._set(record['path'], state == 'missing')
                with self.lock:
                    self.checked += 1
                if self.stopped.wait(self.delay):
                    break
        finally:
            with self.lock:
                self.running = False
                self.completed_at = time.monotonic()

    def stop(self):
        self.stopped.set()
