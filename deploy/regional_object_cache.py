"""Single-owner bounded disk cache for verified immutable object bytes.

Reads hold the cache mutex through consumption: eviction cannot unlink a tile
still being served and thereby hide its retained disk bytes from the budget.
Network adapters yield bounded chunks; no unverified object reaches native code.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from regional_gc import safe_path
from regional_release import sha


class ObjectCache:
    def __init__(self, root, max_bytes, reserve_bytes=256 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes <= 0 or reserve_bytes < 0:
            raise ValueError('invalid object cache budget')
        self.root = Path(root).absolute()
        safe_path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes, self.reserve_bytes = max_bytes, reserve_bytes
        self.lock = threading.Lock()
        self.owner = os.open(safe_path(self.root / '.owner.lock'),
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for path in self.root.iterdir():
                safe_path(path)
                if path.name.startswith('.pending-'):
                    path.unlink()
                elif path.name != '.owner.lock' and not re.fullmatch('[0-9a-f]{64}', path.name):
                    raise ValueError('unexpected object cache entry')
                elif path.name != '.owner.lock' and not path.is_file():
                    raise ValueError('object cache entry is not a file')
            self._make_room(0)
        except BaseException:
            os.close(self.owner)
            self.owner = None
            raise

    def close(self):
        with self.lock:
            if self.owner is not None:
                os.close(self.owner)
                self.owner = None

    def _make_room(self, required):
        files = sorted((p for p in self.root.iterdir() if sha(p.name)),
                       key=lambda p: p.stat().st_mtime_ns)
        used = sum(p.stat().st_size for p in files)
        for path in files:
            if used + required <= self.max_bytes:
                break
            size = path.stat().st_size
            safe_path(path).unlink()
            used -= size
        if used + required > self.max_bytes:
            raise OSError('object exceeds cache budget')
        if shutil.disk_usage(self.root).free < required + self.reserve_bytes:
            raise OSError('object cache reserve exhausted')

    @staticmethod
    def _verified(stream, item):
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
        stream.seek(0)
        return size == item['size'] and digest.hexdigest() == item['sha256']

    @contextmanager
    def open(self, item, fetch):
        if (not sha(item.get('sha256')) or type(item.get('size')) is not int
                or not 0 < item['size'] <= self.max_bytes):
            raise ValueError('invalid or oversized object')
        with self.lock:
            if self.owner is None:
                raise OSError('object cache closed')
            path = safe_path(self.root / item['sha256'])
            if path.exists():
                with path.open('rb') as stream:
                    if self._verified(stream, item):
                        os.utime(path, ns=(time.time_ns(), time.time_ns()))
                        yield stream
                        return
                path.unlink()
            self._make_room(item['size'])
            descriptor, name = tempfile.mkstemp(prefix='.pending-', dir=self.root)
            pending = Path(name)
            try:
                digest, size = hashlib.sha256(), 0
                with os.fdopen(descriptor, 'wb') as stream:
                    chunks = iter(fetch(item['key']))
                    try:
                        for chunk in chunks:
                            size += len(chunk)
                            if size > item['size']:
                                raise ValueError('object exceeds declared size')
                            digest.update(chunk)
                            stream.write(chunk)
                    finally:
                        close = getattr(chunks, 'close', None)
                        if close:
                            close()
                    if size != item['size'] or digest.hexdigest() != item['sha256']:
                        raise ValueError('object size or SHA256 mismatch')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending, path)
                with path.open('rb') as stream:
                    yield stream
            finally:
                pending.unlink(missing_ok=True)
