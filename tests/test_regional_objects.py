import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_object_bridge import Bridge
from regional_object_cache import ObjectCache
from regional_objects import ObjectCatalog
from regional_release import canonical_hash

IMAGE = 'valhalla/valhalla@sha256:' + 'a' * 64
PATH = '2/000/000/001.gph'


def inventory(slug, body):
    digest = hashlib.sha256(body).hexdigest()
    return dict(schema=1, validation='gph-v3-index-v1', slug=slug, image=IMAGE, coverage_sha256='b' * 64,
                graph_fingerprint=canonical_hash({PATH: digest}),
                tiles={PATH: dict(sha256=digest, size=len(body))})


def catalog(*values):
    raws = [json.dumps(v).encode() for v in values]
    return ObjectCatalog([(v, hashlib.sha256(v).hexdigest()) for v in raws], IMAGE)


class ObjectsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cache = ObjectCache(self.root / 'cache', 8, reserve_bytes=0)
        self.a, self.b = inventory('region-a', b'aaaaa'), inventory('region-b', b'bbbbb')
        self.catalog = catalog(self.a, self.b)

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def item(self, value):
        return self.catalog.lookup(value['slug'], value['graph_fingerprint'], PATH)

    def test_same_graphid_different_regions_never_alias_and_budget_evicts(self):
        a, b = self.item(self.a), self.item(self.b)
        self.assertNotEqual(a['key'], b['key'])
        with self.cache.open(a, lambda _: [b'aaaaa']) as stream:
            self.assertEqual(stream.read(), b'aaaaa')
        with self.cache.open(b, lambda _: [b'bbbbb']) as stream:
            self.assertEqual(stream.read(), b'bbbbb')
        self.assertFalse((self.cache.root / a['sha256']).exists())
        self.assertEqual(sum(p.stat().st_size for p in self.cache.root.iterdir()), 5)
        with self.cache.open(b, lambda _: self.fail('hot cache made network request')) as stream:
            self.assertEqual(stream.read(), b'bbbbb')

    def test_wrong_digest_truncation_oversize_and_interrupted_fetch_never_publish(self):
        item = self.item(self.a)
        for chunks in ([b'wrong'], [b'aaa'], [b'aaaaaa']):
            with self.subTest(chunks=chunks), self.assertRaises(ValueError):
                with self.cache.open(item, lambda _: chunks):
                    self.fail('unverified bytes exposed')
            self.assertFalse((self.cache.root / item['sha256']).exists())
            self.assertFalse(list(self.cache.root.glob('.pending-*')))
        closed = []
        def broken(_):
            try:
                yield b'aa'
                raise OSError('network interrupted')
            finally:
                closed.append(True)
        with self.assertRaises(OSError):
            with self.cache.open(item, broken):
                self.fail('interrupted bytes exposed')
        self.assertEqual(closed, [True])
        self.assertFalse(list(self.cache.root.glob('.pending-*')))

    def test_corrupt_local_hit_is_replaced_only_with_verified_exact_bytes(self):
        item = self.item(self.a)
        (self.cache.root / item['sha256']).write_bytes(b'wrong')
        calls = []
        def fetch(key):
            calls.append(key)
            yield b'aaaaa'
        with self.cache.open(item, fetch) as stream:
            self.assertEqual(stream.read(), b'aaaaa')
        self.assertEqual(calls, [item['key']])

    def test_oversized_tile_fails_before_network_and_symlinks_are_rejected(self):
        item = dict(self.item(self.a), size=9)
        with self.assertRaises(ValueError):
            with self.cache.open(item, lambda _: self.fail('must reject before IO')):
                pass
        item = self.item(self.a)
        outside = self.root / 'outside'; outside.write_bytes(b'aaaaa')
        (self.cache.root / item['sha256']).symlink_to(outside)
        with self.assertRaises(ValueError):
            with self.cache.open(item, lambda _: self.fail('symlink accepted')):
                pass
        self.assertEqual(outside.read_bytes(), b'aaaaa')

    def test_owner_exclusion_and_restart_cleanup(self):
        with self.assertRaises(OSError):
            ObjectCache(self.cache.root, 8, 0)
        self.cache.close()
        (self.cache.root / '.pending-interrupted').write_bytes(b'partial')
        self.cache = ObjectCache(self.cache.root, 8, 0)
        self.assertFalse(list(self.cache.root.glob('.pending-*')))

    def test_reader_pins_budget_until_consumption_finishes(self):
        started, finished = threading.Event(), threading.Event()
        def other_reader():
            started.set()
            with self.cache.open(self.item(self.b), lambda _: [b'bbbbb']) as stream:
                self.assertEqual(stream.read(), b'bbbbb')
            finished.set()
        with self.cache.open(self.item(self.a), lambda _: [b'aaaaa']) as stream:
            thread = threading.Thread(target=other_reader)
            thread.start(); self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(.05))
            self.assertEqual(stream.read(), b'aaaaa')
        thread.join(2)
        self.assertTrue(finished.is_set())

    def test_catalog_rejects_modified_manifest_identity_path_and_wrong_image(self):
        raw = json.dumps(self.a).encode()
        with self.assertRaisesRegex(ValueError, 'digest'):
            ObjectCatalog([(raw, '0' * 64)], IMAGE)
        for patch in ({'graph_fingerprint': '0' * 64}, {'image': 'floating:latest'},
                      {'slug': '../escape'}, {'tiles': {'../escape': {'sha256': 'b' * 64, 'size': 1}}}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                catalog(dict(self.a, **patch))
        with self.assertRaises(KeyError):
            self.catalog.lookup('region-a', self.b['graph_fingerprint'], PATH)

    def test_http_bridge_real_cold_hot_boundary_and_failure_status(self):
        calls = []
        def fetch(key):
            calls.append(key)
            yield b'aaaaa'
        bridge = Bridge(self.catalog, self.cache, fetch).start()
        try:
            url = bridge.url('region-a', self.a['graph_fingerprint']).replace('{tilePath}', PATH)
            for _ in range(2):
                with urlopen(url, timeout=2) as response:
                    self.assertEqual(response.read(), b'aaaaa')
            self.assertEqual(len(calls), 1)
            with self.assertRaises(HTTPError) as error:
                urlopen(url.replace('001.gph', '999.gph'), timeout=2)
            self.assertEqual(error.exception.code, 404)
            bad = bridge.url('region-b', self.b['graph_fingerprint']).replace('{tilePath}', PATH)
            with self.assertRaises(HTTPError) as error:
                urlopen(bad, timeout=2)
            self.assertEqual(error.exception.code, 503)
            self.assertEqual(bridge.failures, 1)
        finally:
            bridge.close()


if __name__ == '__main__':
    unittest.main()
