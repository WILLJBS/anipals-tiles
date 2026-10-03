import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from private_archive_verify import verify_stage, validate_stage, read_verified


class Conflict(Exception):
    response = {'Error': {'Code': 'PreconditionFailed'}}


class Store:
    def __init__(self):
        self.objects, self.reads, self.writes = {}, [], []
        self.race = False

    def get_object(self, Bucket, Key):
        self.reads.append(Key)
        raw = self.objects[Key]
        return {'Body': io.BytesIO(raw), 'ContentLength': len(raw)}

    def put_object(self, Bucket, Key, Body, IfNoneMatch, **kw):
        assert IfNoneMatch == '*'
        self.writes.append(Key)
        if self.race:
            self.objects[Key] = b'x' * len(Body)
        if Key in self.objects:
            raise Conflict()
        self.objects[Key] = Body


class PrivateArchiveTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(); data = b'private source'
        sha = hashlib.sha256(data).hexdigest()
        self.entry = {'path': '.local/source.json', 'purpose': 'source', 'classification': 'private',
                      'sha256': sha, 'bytes': len(data), 'key': f'archive/sha256/{sha[:2]}/{sha}',
                      'uploaded': True, 'verified': False}
        self.stage = {'schema': 'anipals-private-archive-stage-v1', 'complete': False,
                      'uploadsComplete': True, 'bucket': 'private-archive', 'inputSha256': '1'*64,
                      'plannedFiles': 1, 'plannedBytes': len(data), 'entries': [self.entry]}
        self.store.objects[self.entry['key']] = data

    def run_stage(self, stage=None):
        raw = json.dumps(stage or self.stage).encode(); sha = hashlib.sha256(raw).hexdigest()
        key = f'archive/staging/sha256/{sha}.json'; self.store.objects[key] = raw
        return verify_stage(self.store, 'private-archive', manifest_key=key, manifest_sha=sha,
                            manifest_bytes=len(raw), total_bytes=self.stage['plannedBytes'], source_sha='a'*40)

    def test_complete_gets_only_then_private_receipt_and_resume(self):
        result = self.run_stage(); receipt = json.loads(self.store.objects[result['key']])
        self.assertTrue(receipt['complete']); self.assertEqual(receipt['schema'], 'anipals-private-archive-v1')
        self.assertTrue(receipt['entries'][0]['verified'])
        self.assertEqual(receipt['entries'][0]['objectRef'], 'r2://private-archive/'+self.entry['key'])
        self.assertTrue(result['key'].startswith('archive/receipts/sha256/'))
        self.assertEqual(self.store.reads[1], self.entry['key'])
        self.assertEqual(self.run_stage(), result)

    def test_same_length_corruption_never_publishes_receipt(self):
        self.store.objects[self.entry['key']] = b'x' * self.entry['bytes']
        with self.assertRaisesRegex(ValueError, 'CONTENT_MISMATCH'):
            self.run_stage()
        self.assertEqual(self.store.writes, [])

    def test_truncation_and_oversized_objects_fail_closed(self):
        for data in (b'short', b'x' * (self.entry['bytes'] + 1)):
            self.store.objects[self.entry['key']] = data
            with self.assertRaisesRegex(ValueError, 'LENGTH_MISMATCH'):
                self.run_stage()
            self.assertEqual(self.store.writes, [])

    def test_stage_tampering_and_premature_verified_flags_rejected(self):
        for patch_values in ({'complete': True}, {'uploadsComplete': False}, {'bucket': 'elsewhere'},
                             {'plannedFiles': 2}, {'plannedBytes': 0}, {'schema': 'anipals-private-archive-v1'}):
            with self.assertRaises(ValueError):
                self.run_stage(self.stage | patch_values)
        for values in ({'path': '../secret'}, {'path': '/secret'}, {'verified': True}, {'objectRef': 'r2://x/y'},
                       {'key': 'public/other'}, {'classification': 'public'}, {'bytes': True}):
            stage = copy.deepcopy(self.stage); stage['entries'][0].update(values)
            with self.assertRaises(ValueError):
                self.run_stage(stage)
        self.assertEqual(self.store.writes, [])

    def test_exact_manifest_identity_and_budget_required_before_sources(self):
        with self.assertRaisesRegex(ValueError, 'IDENTITY_REQUIRED'):
            verify_stage(self.store, 'private-archive', manifest_key='archive/staging/other',
                         manifest_sha='0'*64, manifest_bytes=1, total_bytes=0, source_sha='a'*40)
        with self.assertRaisesRegex(ValueError, 'BUDGET'):
            validate_stage(self.stage, 'private-archive', 0)
        self.assertEqual(self.store.reads, [])

    def test_duplicate_source_paths_rejected_but_identical_objects_verify_once(self):
        stage = copy.deepcopy(self.stage); stage['entries'].append(copy.deepcopy(self.entry))
        stage['plannedFiles'] = 2; stage['plannedBytes'] *= 2
        with self.assertRaisesRegex(ValueError, 'DUPLICATE'):
            validate_stage(stage, 'private-archive', stage['plannedBytes'])
        stage['entries'][1]['path'] = '.local/alias.json'; self.stage = stage
        self.run_stage()
        self.assertEqual(self.store.reads.count(self.entry['key']), 1)

    def test_concurrent_corrupt_receipt_cannot_be_reported_success(self):
        self.store.race = True
        with self.assertRaisesRegex(ValueError, 'CONTENT_MISMATCH'):
            self.run_stage()

    def test_network_body_retry_is_bounded_and_checks_full_bytes(self):
        original = self.store.get_object; calls = []
        def flaky(**kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise OSError('transient')
            return original(**kwargs)
        self.store.get_object = flaky
        with patch('private_archive_verify.time.sleep'):
            read_verified(self.store, 'private-archive', self.entry['key'], self.entry['sha256'], self.entry['bytes'])
        self.assertEqual(len(calls), 3)
        self.store.get_object = lambda **kw: (_ for _ in ()).throw(OSError('still failing'))
        with patch('private_archive_verify.time.sleep') as sleep:
            with self.assertRaises(OSError):
                read_verified(self.store, 'private-archive', self.entry['key'], self.entry['sha256'], self.entry['bytes'])
            self.assertEqual(sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()
