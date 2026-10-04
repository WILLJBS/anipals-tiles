"""Bounded upload ownership: queued and running tile files share one byte budget."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
import tempfile
import time

MAX_PENDING_BYTES = 512 * 1024 * 1024
MAX_WORKERS = 8


class UploadQueue:
    def __init__(self, work, upload, *, workers=MAX_WORKERS, max_bytes=MAX_PENDING_BYTES,
                 progress=lambda event: None):
        if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
            raise ValueError('upload workers outside bounded policy')
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PENDING_BYTES:
            raise ValueError('upload bytes outside bounded policy')
        self.work, self.upload, self.workers, self.max_bytes = work, upload, workers, max_bytes
        self.progress, self.pending = progress, {}
        self.pending_bytes = self.peak_bytes = self.peak_files = self.sequence = 0
        self.completed = self.completed_bytes = 0
        self.started = self.last_progress = time.monotonic()

    def __enter__(self):
        self.folder = tempfile.TemporaryDirectory(prefix='region-uploads-', dir=self.work)
        self.executor = ThreadPoolExecutor(max_workers=self.workers)
        try:
            self.report('migration_upload_started')
        except BaseException:
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.folder.cleanup()
            raise
        return self

    def report(self, event):
        self.progress(dict(event=event, completedTiles=self.completed,
            completedBytes=self.completed_bytes, pendingTiles=len(self.pending),
            pendingBytes=self.pending_bytes, peakPendingBytes=self.peak_bytes,
            peakPendingTiles=self.peak_files, workers=self.workers,
            elapsedMs=round((time.monotonic()-self.started)*1000)))
        self.last_progress = time.monotonic()

    def collect(self, block=False):
        if not self.pending: return
        done = wait(self.pending, timeout=5, return_when=FIRST_COMPLETED)[0] if block else {
            future for future in self.pending if future.done()}
        for future in done:
            path, size = self.pending.pop(future)
            self.pending_bytes -= size
            future.result()  # A single failed object invalidates the entire region.
            self.completed += 1; self.completed_bytes += size
        if time.monotonic()-self.last_progress >= 5:
            self.report('migration_upload_progress')

    def submit(self, relative, source, item):
        size = item['size']
        if type(size) is not int or not 0 < size <= self.max_bytes:
            raise ValueError('tile exceeds upload queue byte budget')
        self.collect()
        while self.pending_bytes+size > self.max_bytes or len(self.pending) >= self.workers*2:
            self.collect(block=True)
        if source.stat().st_size != size:
            raise ValueError('upload queue source size changed')
        owned = Path(self.folder.name)/('tile-%08d.gph' % self.sequence)
        self.sequence += 1
        # Transfer file ownership before tar traversal reuses current.gph.
        source.replace(owned)
        owned_item = dict(item)
        def execute():
            try:
                return self.upload(relative, owned, owned_item)
            finally:
                owned.unlink(missing_ok=True)
        try:
            future = self.executor.submit(execute)
        except BaseException:
            owned.unlink(missing_ok=True)
            raise
        self.pending[future] = owned, size
        self.pending_bytes += size
        self.peak_bytes = max(self.peak_bytes, self.pending_bytes)
        self.peak_files = max(self.peak_files, len(self.pending))

    def finish(self):
        while self.pending:
            self.collect(block=True)
        self.report('migration_upload_complete')

    def __exit__(self, kind, error, traceback):
        try:
            if kind is None:
                self.finish()
        finally:
            for future in self.pending:
                future.cancel()
            self.executor.shutdown(wait=True, cancel_futures=True)
            # Includes canceled futures whose execute/finally never ran.
            for path, _ in self.pending.values():
                path.unlink(missing_ok=True)
            self.pending.clear(); self.pending_bytes = 0
            self.folder.cleanup()
        return False
