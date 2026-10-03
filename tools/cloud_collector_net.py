"""Swap only the range persistence layer; retain collector HTTP/geometry/review guards."""
import hashlib
import json
import threading
import time


def cloud_range_class(base, journal, deadline):
    class CloudRanges(base):
        def __init__(self, root, proxy=None, budget=None, read_only=()):
            if proxy or read_only or budget != journal.budget:
                raise ValueError('CLOUD_NETWORK_CONFIGURATION_DIFFERS')
            super().__init__(root, None, budget, ())
            self.calls = [{'body_bytes': journal.used}]
            self._cloud_lock = threading.RLock()

        def get(self, url, start, end):
            with self._cloud_lock:
                if time.monotonic() >= deadline: raise TimeoutError('CLOUD_JOB_DEADLINE')
                if start < 0 or not 0 < end-start+1 <= 20_000_000: raise ValueError('CLOUD_RANGE_BOUND')
                for item in journal.ranges.values():
                    low, high = item['meta']['range']
                    if item['meta']['url'] == url and low <= start and high >= end:
                        expected = hashlib.sha256(f'{url}:{low}:{high}'.encode()).hexdigest()
                        if (item['cacheKey'] != expected or high-low+1 != item['blob']['bytes']
                                or item['meta']['sha256'] != item['blob']['sha256']):
                            raise ValueError('CLOUD_CACHE_IDENTITY_MISMATCH')
                        path = self.root/(item['cacheKey']+'.bin')
                        try:
                            journal.store.fetch(item['blob'], path)
                            with path.open('rb') as stream:
                                stream.seek(start-low); body = stream.read(end-start+1)
                            if len(body) != end-start+1: raise ValueError('CLOUD_CACHE_SHORT_READ')
                            return body
                        finally: path.unlink(missing_ok=True)
                key = hashlib.sha256(f'{url}:{start}:{end}'.encode()).hexdigest()
                token = journal.reserve(3*(end-start+1)); before = len(self.calls)
                cached = None
                try:
                    body = super().get(url, start, end)
                    path = self.root/(key+'.bin'); meta = json.loads(path.with_suffix('.json').read_text())
                    cached = {'cacheKey': key, 'meta': meta, 'blob': journal.store.put(path)}
                    return body
                finally:
                    actual = sum(c['body_bytes'] for c in self.calls[before:])
                    journal.settle(token, actual, cached)
                    (self.root/(key+'.bin')).unlink(missing_ok=True)
                    (self.root/(key+'.json')).unlink(missing_ok=True)
    return CloudRanges


def wrap_collect(original, journal):
    def collect(con, net, root, url, size, regions, theme):
        prefix = hashlib.sha256(url.encode()).hexdigest()[:20]
        names = [name for name in journal.files if name.startswith(prefix+'-') and name.endswith('.json')]
        for name in names: journal.store.fetch(journal.files[name], root/name)
        try:
            for item in original(con, net, root, url, size, regions, theme): yield item
            for path in root.glob(prefix+'-*.json'):
                if len(path.stem) == 37: journal.file(path)
        finally:
            # The immutable file JSON is the resume checkpoint. These SQLite DBs
            # are scratch stores rebuilt by the unchanged collector each time.
            for suffix in ('-records.sqlite', '-records.sqlite-journal', '-records.lock'):
                (root/(prefix+suffix)).unlink(missing_ok=True)
            for name in names: (root/name).unlink(missing_ok=True)
            for path in root.glob(prefix+'-*.json'):
                if len(path.stem) == 37 and path.name in journal.files: path.unlink()
    return collect
