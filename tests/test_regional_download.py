import hashlib
import http.server
import io
import json
import os
import struct
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
import regional_download as download
import regional_storage as storage

IMAGE = 'valhalla/valhalla@sha256:' + 'a' * 64


def tile(poison=False, tail=0):
    data = bytearray(376)
    struct.pack_into('<Q', data, 0, 10)
    data[16:22] = b'3.3.0\0'
    struct.pack_into('<Q', data, 40, 1 | (1 << 21))
    struct.pack_into('<4I', data, 96, 360, 360, 360, 376)
    struct.pack_into('<25I', data, 116, *([1] * 25))
    struct.pack_into('<III', data, 216, 376, 0, 376)
    struct.pack_into('<Q', data, 280, (5 if poison else 0) | (1 << 21))
    struct.pack_into('<Q', data, 304, 10)
    struct.pack_into('<Q', data, 352, 10)
    data[-1] = tail
    return bytes(data)


def archive(body, unsafe=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        member = tarfile.TarInfo('tiles/../escape' if unsafe else 'tiles/2/000/000/001.gph')
        member.size = len(body)
        tar.addfile(member, io.BytesIO(body))
    return output.getvalue()


def fixture(body=None):
    body = archive(tile()) if body is None else body
    chunks = [body[:777], body[777:]]  # non-header-aligned part boundary
    slug = 'north-america-canada'
    assets = [dict(name='READY')]
    payloads = {}
    for i, chunk in enumerate(chunks):
        name = 'tiles-%s.tar-%02d' % (slug, i)
        url = 'https://example.invalid/' + name
        payloads[url] = chunk
        assets.append(dict(name=name, browser_download_url=url, size=len(chunk), digest='sha256:' + hashlib.sha256(chunk).hexdigest()))
    release = dict(tag_name='tiles-test-1', draft=False, prerelease=False, assets=assets)
    roster = {'region': [dict(slug=slug, region='north-america/canada')]}
    return release, roster, payloads


class RegionalDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.release, self.roster, self.payloads = fixture()
        self.plan = download.build_plans(self.release, self.roster, IMAGE)[0]

    def tearDown(self):
        self.temp.cleanup()

    def writer(self, path, part):
        path.write_bytes(self.payloads[part['url']])

    def prepare(self, **kwargs):
        return download.prepare_region(self.root, self.plan, downloader=self.writer, reserve_bytes=0, **kwargs)

    def health(self, descriptor):
        return dict(slug=descriptor['slug'], fingerprint=descriptor['fingerprint'], verified=True)

    def test_complete_atomic_marker_restart_skips_io_and_no_joined_tar(self):
        descriptor = self.prepare()
        self.assertEqual(descriptor['tile_count'], 1)
        self.assertFalse(Path(descriptor['tile_dir']).is_absolute())
        self.assertEqual((self.root / descriptor['tile_dir'] / '2/000/000/001.gph').read_bytes(), tile())
        self.assertFalse(list(self.root.rglob('*.tar')))
        self.assertFalse(list(self.root.rglob('part-*')))
        with patch.object(download, 'validate_tiles', side_effect=AssertionError('no repeat walk')):
            complete = download.prepare_region(self.root, self.plan, downloader=lambda *_: self.fail('no repeat download'))
        self.assertEqual(complete, descriptor)
        self.assertFalse((self.root / 'regions' / self.plan['slug'] / 'active.json').exists())
        download.activate_region(self.root, descriptor, self.health(descriptor))
        self.assertEqual(json.loads((self.root / 'regions' / self.plan['slug'] / 'active.json').read_text())['fingerprint'], self.plan['fingerprint'])

    def test_half_download_restart_keeps_bytes_and_fingerprint_cache(self):
        attempts = []
        def interrupted(path, part):
            body = self.payloads[part['url']]
            path.write_bytes(body[:100]); raise OSError('interrupted')
        with self.assertRaises(OSError):
            download.prepare_region(self.root, self.plan, downloader=interrupted, reserve_bytes=0)
        cache = download.cache_path(self.root, self.plan)
        self.assertEqual((cache / 'part-00').stat().st_size, 100)
        def resumed(path, part):
            attempts.append(path.stat().st_size if path.exists() else 0)
            self.writer(path, part)
        descriptor = download.prepare_region(self.root, self.plan, downloader=resumed, reserve_bytes=0)
        self.assertEqual(attempts[0], 100)
        self.assertEqual(descriptor['tile_count'], 1)

    def test_wrong_digest_or_poisoned_graph_never_gets_complete(self):
        with self.assertRaisesRegex(ValueError, 'digest'):
            download.prepare_region(self.root, self.plan, downloader=lambda path, _: path.write_bytes(b'bad'), reserve_bytes=0)
        self.assertFalse(list(self.root.rglob('.complete.json')))
        self.release, self.roster, self.payloads = fixture(archive(tile(poison=True)))
        self.plan = download.build_plans(self.release, self.roster, IMAGE)[0]
        with self.assertRaisesRegex(ValueError, 'edge span'):
            self.prepare()
        self.assertFalse(list(self.root.rglob('.complete.json')))

    def test_crash_after_directory_rename_repairs_without_exposing_incomplete(self):
        original = download.atomic_json
        def fail_marker(path, value):
            if Path(path).name == '.complete.json': raise OSError('power loss')
            return original(path, value)
        with patch.object(download, 'atomic_json', side_effect=fail_marker), self.assertRaises(OSError):
            self.prepare()
        self.assertFalse(list(self.root.rglob('.complete.json')))
        self.assertTrue(list(self.root.rglob('.identity.json')))
        self.assertEqual(self.prepare()['tile_count'], 1)

    def test_capacity_fails_before_fetch_and_stale_stage_does_not_deadlock_retry(self):
        with patch.object(download, 'require_capacity', side_effect=OSError('insufficient disk')):
            with self.assertRaises(OSError):
                download.prepare_region(self.root, self.plan, downloader=lambda *_: self.fail('must not fetch'), reserve_bytes=0)
        stage = self.root / 'regions' / self.plan['slug'] / ('.' + self.plan['fingerprint'] + '.staging')
        stage.mkdir(); (stage / 'stale').write_bytes(b'x')
        def check(root, required, reserve):
            self.assertFalse(stage.exists(), 'reclaim unexposed extraction before capacity check')
            self.assertEqual(required, sum(p['size'] for p in self.plan['parts']) * 2)
        with patch.object(download, 'require_capacity', side_effect=check):
            self.assertEqual(self.prepare()['tile_count'], 1)

    def test_legacy_cleanup_health_guard_durable_authorization_and_restart(self):
        descriptor = self.prepare()
        legacy = self.root / 'tiles'; legacy.mkdir(); (legacy / 'mixed.gph').write_bytes(b'legacy')
        self.assertFalse(download.cleanup_legacy(self.root, descriptor, lambda _: {'verified': True, 'slug': descriptor['slug'], 'fingerprint': 'wrong'}))
        self.assertTrue(legacy.exists())
        original = download.shutil.rmtree
        def crash_on_trash(path, *args, **kwargs):
            if Path(path).name == '.legacy-trash': raise OSError('reboot during deletion')
            return original(path, *args, **kwargs)
        with patch.object(download.shutil, 'rmtree', side_effect=crash_on_trash), self.assertRaises(OSError):
            download.cleanup_legacy(self.root, descriptor, self.health)
        journal = self.root / 'regions/migration.json'
        self.assertEqual(json.loads(journal.read_text())['state'], 'cleanup_authorized')
        self.assertTrue((self.root / 'regions/.legacy-trash/mixed.gph').exists())
        self.assertFalse(download.cleanup_legacy(self.root, descriptor, lambda _: {'verified': False}))
        self.assertTrue(download.cleanup_legacy(self.root, descriptor, self.health))
        self.assertEqual(json.loads(journal.read_text())['state'], 'cleaned')
        self.assertFalse((self.root / 'regions/.legacy-trash').exists())
        with patch.dict(os.environ, {'RESET_TILES': '1'}):
            self.assertEqual(download.prepare_region(self.root, self.plan), descriptor)

    def test_part_adoption_exact_registry_and_distinct_fingerprints(self):
        old = self.root / 'tiles'; old.mkdir()
        oldpart = old / ('tiles-%s.part-0' % self.plan['slug']); oldpart.write_bytes(b'prefix')
        download.adopt_legacy_parts(self.root, [self.plan])
        self.assertFalse(oldpart.exists())
        self.assertEqual((download.cache_path(self.root, self.plan) / 'part-00').read_bytes(), b'prefix')
        other = json.loads(json.dumps(self.release)); other['tag_name'] = 'tiles-test-2'
        nextplan = download.build_plans(other, self.roster, IMAGE)[0]
        self.assertNotEqual(nextplan['fingerprint'], self.plan['fingerprint'])
        bad = json.loads(json.dumps(self.release)); bad['assets'][1]['digest'] = 'bad'
        with self.assertRaises(ValueError): download.build_plans(bad, self.roster, IMAGE)
        with self.assertRaises(ValueError): download.build_plans(self.release, {'region': []}, IMAGE)

    def test_unsafe_tar_members_fail_and_part_stream_is_bounded(self):
        with self.assertRaises((ValueError, tarfile.ReadError)):
            storage.extract_parts([], self.root / 'empty', 0)
        unsafe = archive(tile(), unsafe=True); file = self.root / 'part'; file.write_bytes(unsafe)
        with self.assertRaisesRegex(ValueError, 'unsafe archive'):
            storage.extract_parts([file], self.root / 'extract', len(unsafe))
        with storage.PartStream([file]) as stream:
            with self.assertRaises(ValueError): stream.read()
        self.assertFalse((self.root / 'escape').exists())

    def test_loop_activates_only_after_current_native_verification(self):
        descriptor = self.prepare()
        with patch.object(download, 'prepare_region', return_value=descriptor):
            self.assertEqual(download.run_pass(self.root, [self.plan], lambda _: {'verified': False}), [self.plan['slug']])
            self.assertFalse((self.root / 'regions' / self.plan['slug'] / 'active.json').exists())
            self.assertEqual(download.run_pass(self.root, [self.plan], self.health), [])
        self.assertTrue((self.root / 'regions' / self.plan['slug'] / 'active.json').exists())

    def test_actual_download_helper_complete_part_skip_and_range_to_stream_tar(self):
        requests = []
        payloads = {url.rsplit('/', 1)[1]: value for url, value in self.payloads.items()}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('Range')))
                body = payloads[self.path.lstrip('/')]
                offset = int(self.headers.get('Range', 'bytes=0-')[6:-1])
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length', str(len(body)-offset))
                if offset:
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (offset,len(body)-1,len(body)))
                self.end_headers(); self.wfile.write(body[offset:])
            def log_message(self, *_): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for asset in self.release['assets'][1:]:
                asset['browser_download_url'] = 'http://127.0.0.1:%d/%s' % (server.server_port,asset['name'])
            plan = download.build_plans(self.release,self.roster,IMAGE)[0]
            cache = download.cache_path(self.root,plan); cache.mkdir(parents=True)
            (cache/'part-00').write_bytes(payloads[plan['parts'][0]['name']])
            (cache/'part-01').write_bytes(payloads[plan['parts'][1]['name']][:100])
            descriptor = download.prepare_region(self.root,plan,reserve_bytes=0)
            self.assertEqual(descriptor['tile_count'],1)
            self.assertEqual(requests,[('/'+plan['parts'][1]['name'],'bytes=100-')])
            self.assertFalse(list(self.root.rglob('*.tar')))
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
