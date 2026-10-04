"""Opt-in pinned remote release with shared storage ownership enforcement."""
import json
import os
from pathlib import Path
import threading

from regional_composite import load_composite
from regional_download import activate_region
from regional_engine import EngineError
from regional_object_bridge import Bridge
from regional_object_cache import ObjectCache
from regional_r2 import reader


class RemoteRuntime:
    def __init__(self, root, base_coverage, environ=None):
        env = os.environ if environ is None else environ
        self.root = Path(root).resolve()
        self.fetch = reader(env)
        image = Path('/usr/local/share/anipals-valhalla-image.txt').read_text().strip()
        self.composite = load_composite(self.fetch, env['ANIPALS_REMOTE_INDEX_SHA256'],
            base_coverage, image, self.root / 'object-release-cache')
        budget = int(env.get('ANIPALS_REMOTE_CACHE_BYTES', str(4 * 1024**3)))
        if not 64 * 1024**2 <= budget <= 16 * 1024**3:
            raise ValueError('remote cache budget must be 64 MiB through 16 GiB')
        self.composite.install_ownership(self.root)
        self.cache = ObjectCache(self.root / 'object-cache', budget)
        self.bridge = Bridge(self.composite.objects, self.cache, self.fetch).start()
        self.stop = threading.Event()
        self.thread = None

    def start(self, router):
        def install():
            pending = set(self.composite.rows)
            while pending and not self.stop.is_set():
                for slug in sorted(pending):
                    if self.stop.is_set():
                        return
                    try:
                        descriptor = self.composite.prepare(self.root, slug)
                        from regional_ownership import allows
                        if not allows(self.root, descriptor):
                            pending.remove(slug)
                            continue  # Explicit rollback persists across restarts.
                        graph = self.composite.objects.graphs[(slug, descriptor['object_fingerprint'])]
                        # A declared object must be readable before native activation;
                        # an R2 outage cannot masquerade as absent pedestrian edges.
                        first = sorted(graph['tiles'])[0]
                        item = self.composite.objects.lookup(slug, descriptor['object_fingerprint'], first)
                        with self.cache.open(item, self.fetch):
                            pass
                        verified = router.verify(slug, descriptor['fingerprint'])
                        activate_region(self.root, descriptor, verified,
                                        self.composite.digest if self.composite.schema == 2 else None)
                        pending.remove(slug)
                        print(json.dumps(dict(event='remote_graph_activated', region=slug,
                                              fingerprint=descriptor['fingerprint'])), flush=True)
                    except (EngineError, OSError, ValueError, KeyError):
                        print(json.dumps(dict(event='remote_graph_unavailable', region=slug)), flush=True)
                if pending:
                    self.stop.wait(30)
        self.thread = threading.Thread(target=install, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.bridge.close()
        self.cache.close()
