import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from cloud_collector_io import PrivateStore, Journal, fingerprint
from cloud_collector_net import cloud_range_class
from cloud_collect_places import validate, unpack
from cloud_execution import github_execution, REPOSITORY, WORKFLOW


class Conflict(Exception): response = {'Error': {'Code': '412'}}


class S3:
    def __init__(self): self.objects = {}
    def put_object(self, Bucket, Key, Body, IfNoneMatch, **unused):
        assert IfNoneMatch == '*'
        if Key in self.objects: raise Conflict()
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()
    def get_object(self, Bucket, Key):
        raw = self.objects[Key]; return {'Body': io.BytesIO(raw), 'ContentLength': len(raw)}
    def list_objects_v2(self, Bucket, Prefix, **unused):
        return {'Contents': [{'Key': k, 'Size': len(v)} for k, v in self.objects.items() if k.startswith(Prefix)]}


def upload(client, bucket, key, path, size, sha, metadata):
    raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == sha and len(raw) == size
    try: client.put_object(Bucket=bucket, Key=key, Body=raw, IfNoneMatch='*')
    except Conflict: pass


class BaseRanges:
    def __init__(self, root, proxy, budget, read_only):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True); self.calls = []
    def get(self, url, start, end):
        raw = b'abcdefgh'[start:end+1]; self.calls.append({'body_bytes': len(raw)})
        key = hashlib.sha256(f'{url}:{start}:{end}'.encode()).hexdigest()
        (self.root/(key+'.bin')).write_bytes(raw)
        (self.root/(key+'.json')).write_text(json.dumps({'url': url, 'range': [start, end], 'sha256': hashlib.sha256(raw).hexdigest()}))
        return raw


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.s3 = S3(); self.store = PrivateStore(self.s3, 'private-test', upload)
    def journal(self, budget=100): return Journal(self.store, 'a'*64, budget)

    def test_execution_requires_consistent_github_owned_context(self):
        sha = 'b'*40; ref = 'refs/heads/staging'
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_SHA': sha,
               'GITHUB_WORKFLOW_SHA': sha, 'GITHUB_REF': ref, 'GITHUB_RUN_ID': '123',
               'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_WORKFLOW_REF': f'{REPOSITORY}/{WORKFLOW}@{ref}'}
        result = github_execution(sha, env); self.assertEqual(result['runId'], 123)
        for key in env:
            with self.assertRaisesRegex(ValueError, 'EXECUTION_CONTEXT'):
                github_execution(sha, env | {key: 'invalid'})
        with self.assertRaises(ValueError): github_execution(sha, {})

    def test_write_ahead_reservation_survives_process_loss(self):
        j = self.journal(); token = j.reserve(24)
        self.assertEqual(self.journal().used, 24)
        j.settle(token, 7); self.assertEqual(self.journal().used, 7)
        j.snapshot(); self.assertEqual(self.journal().used, 7)
    def test_exhaustion_precedes_network_and_unsettled_spend_never_disappears(self):
        j = self.journal(23)
        c = cloud_range_class(BaseRanges, j, time.monotonic()+100)(self.root/'ranges', budget=23)
        with self.assertRaisesRegex(ValueError, 'BUDGET'): c.get('https://source', 0, 7)
        self.assertEqual(c.calls, [{'body_bytes': 0}])
        j.reserve(23)
        with self.assertRaisesRegex(ValueError, 'BUDGET'): self.journal(22)
    def test_range_resume_preserves_bytes_without_network_or_local_disk_growth(self):
        j = self.journal(); c = cloud_range_class(BaseRanges, j, time.monotonic()+100)(self.root/'ranges', budget=100)
        self.assertEqual(c.get('https://source', 0, 7), b'abcdefgh'); self.assertEqual(j.used, 8)
        self.assertEqual(list((self.root/'ranges').glob('*.bin')), [])
        restored = self.journal(); resumed = cloud_range_class(BaseRanges, restored, time.monotonic()+100)(self.root/'again', budget=100)
        self.assertEqual(resumed.get('https://source', 2, 4), b'cde')
        self.assertEqual(restored.used, 8); self.assertEqual(resumed.calls, [{'body_bytes': 8}])
    def test_corrupt_remote_range_is_not_refetched_or_accepted(self):
        j = self.journal(); c = cloud_range_class(BaseRanges, j, time.monotonic()+100)(self.root/'ranges', budget=100)
        c.get('https://source', 0, 7); ref = next(iter(j.ranges.values()))['blob']
        self.s3.objects[ref['key']] = b'xxxxxxxx'
        with self.assertRaisesRegex(ValueError, 'SHA'): c.get('https://source', 0, 7)
        self.assertEqual(j.used, 8)
    def test_covering_cache_metadata_cannot_claim_more_bytes_than_verified_blob(self):
        j = self.journal(); c = cloud_range_class(BaseRanges, j, time.monotonic()+100)(self.root/'ranges', budget=100)
        c.get('https://source', 0, 7); item = next(iter(j.ranges.values()))
        item['meta']['range'][1] = 12
        with self.assertRaisesRegex(ValueError, 'CACHE_IDENTITY'):
            c.get('https://source', 2, 10)

    def test_duplicate_before_compaction_snapshot_still_fails(self):
        j = self.journal(); token = j.reserve(3); j.settle(token, 1); j.snapshot()
        self.store.save_json(j.prefix+'journal/000000000001-', {'kind': 'reserve', 'id': 'other', 'upper': 2})
        with self.assertRaisesRegex(ValueError, 'CONCURRENT'):
            self.journal()

    def test_completed_file_is_a_separate_verified_checkpoint(self):
        j = self.journal(); path = self.root/'checkpoint.json'; path.write_text('{"places":[]}')
        desc = j.file(path); again = self.journal()
        self.assertEqual(again.files[path.name], desc); self.assertEqual(again.ranges, {})
    def test_invalid_settlement_and_concurrent_writer_fail_closed(self):
        j = self.journal(); token = j.reserve(3)
        with self.assertRaisesRegex(ValueError, 'CHARGE'): j.settle(token, 4)
        # Invalid event itself is durable and makes resume fail instead of hiding it.
        with self.assertRaises(ValueError): self.journal()
    def test_sdk_none_response_retries_only_three_times_without_attribute_error(self):
        class Transport(OSError): response = None
        calls = []
        def fail(**unused): calls.append(1); raise Transport('private transport context')
        self.s3.put_object = fail
        with patch('cloud_collector_io.time.sleep'):
            with self.assertRaises(Transport): self.store.save_json('private/', {'ok': False})
        self.assertEqual(len(calls), 3)

    def test_duplicate_journal_sequence_is_not_silently_skipped(self):
        j = self.journal(); j.reserve(3)
        self.store.save_json(j.prefix+'journal/000000000001-', {'kind': 'reserve', 'id': 'other', 'upper': 2})
        with self.assertRaisesRegex(ValueError, 'CONCURRENT'):
            self.journal()

    def test_publication_or_oversized_pilot_cannot_be_enabled(self):
        desc = {'key': 'archive/sha256/aa/'+'a'*64, 'sha256': 'a'*64, 'bytes': 1}
        s = {'schema': 'anipals-cloud-collector-v1', 'mode': 'pilot', 'globalScopeCount': 6222,
             'workers': 1, 'themes': ['base', 'places'], 'publicationsAllowed': False, 'seeds': [],
             'priorSourceBytes': 0, 'selectedNames': ['A'], 'selectedGeoNamesIds': ['123'],
             'sourceBudgetBytes': 100, 'maxMinutes': 25, 'appSourceSha': 'b'*40,
             'roster': desc, 'index': desc, 'code': desc}
        validate(s)
        for change in ({'publicationsAllowed': True}, {'selectedNames': list(map(str, range(21)))},
                       {'workers': 4}, {'seeds': [{}]}, {'sourceBudgetBytes': -1}, {'themes': ['base']}):
            with self.assertRaises(ValueError): validate(s | change)
    def test_private_blob_corruption_removes_partial_file(self):
        raw = b'expected'; sha = hashlib.sha256(raw).hexdigest(); key = 'archive/sha256/'+sha[:2]+'/'+sha
        self.s3.objects[key] = b'bad'; target = self.root/'download'
        with self.assertRaisesRegex(ValueError, 'SHA'):
            self.store.fetch({'key': key, 'sha256': sha, 'bytes': len(raw)}, target)
        self.assertFalse(target.exists()); self.assertFalse(target.with_suffix('.download').exists())


if __name__ == '__main__': unittest.main()
