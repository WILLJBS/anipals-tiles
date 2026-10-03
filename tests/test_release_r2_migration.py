import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'deploy'))
from migration_stream import inventory, migrate
from migration_r2 import Publisher
from test_regional_download import tile


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, body in entries:
            member = tarfile.TarInfo(name); member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
    return output.getvalue()


def source(body):
    # A tile body deliberately straddles independently authenticated parts.
    pieces = [body[:777], body[777:1501], body[1501:]]
    parts = [dict(size=len(p), sha256=hashlib.sha256(p).hexdigest(), name=str(i)) for i, p in enumerate(pieces)]
    def download(path, part):
        path.write_bytes(pieces[int(part['name'])])
        # Only the current shard and current tile may exist on disk.
        assert len(list(path.parent.glob('part-*'))) == 1
    return dict(parts=parts), download


class MigrationTests(unittest.TestCase):
    def test_two_pass_stream_matches_whole_graph_validator_and_bounds_disk(self):
        body = tile()
        plan, download = source(archive([('tiles/2/000/000/001.gph', body)]))
        with tempfile.TemporaryDirectory() as root:
            tiles, headers = inventory(plan, root, download)
            received = []
            result = migrate(plan, root, tiles, headers,
                lambda name, path, item: received.append((name, path.read_bytes(), item)), downloader=download)
            self.assertEqual(result, {'tiles': 1, 'external_references': 0})
            self.assertEqual(received[0][1], body)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_bad_part_or_late_poison_never_uploads_unknown_bytes(self):
        for poison in ('part', 'tile'):
            plan, download = source(archive([('tiles/2/000/000/001.gph', tile(poison=poison=='tile'))]))
            with tempfile.TemporaryDirectory() as root:
                if poison == 'part':
                    plan['parts'][0]['sha256'] = '0' * 64
                    with self.assertRaisesRegex(ValueError, 'shard'):
                        inventory(plan, root, download)
                else:
                    tiles, headers = inventory(plan, root, download)
                    with self.assertRaisesRegex(ValueError, 'edge span'):
                        migrate(plan, root, tiles, headers, lambda *_: self.fail('poison uploaded'), downloader=download)
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_path_escape_duplicate_and_header_mismatch_fail_first_pass(self):
        examples = [[('tiles/../escape', tile())],
                    [('tiles/2/000/000/001.gph', tile())]*2,
                    [('tiles/2/000/000/002.gph', tile())]]
        for entries in examples:
            plan, download = source(archive(entries))
            with tempfile.TemporaryDirectory() as root, self.assertRaises(ValueError):
                inventory(plan, root, download)

    def test_cross_tile_bad_index_uses_complete_first_pass_header_inventory(self):
        first, second = bytearray(tile()), bytearray(tile())
        struct.pack_into('<Q', first, 304, 18 | (1 << 25))
        struct.pack_into('<Q', second, 0, 18)
        struct.pack_into('<Q', second, 352, 18)
        plan, download = source(archive([('tiles/2/000/000/001.gph', first), ('tiles/2/000/000/002.gph', second)]))
        with tempfile.TemporaryDirectory() as root:
            tiles, headers = inventory(plan, root, download)
            with self.assertRaisesRegex(ValueError, 'nodes index 1 out of bounds 1'):
                migrate(plan, root, tiles, headers, lambda *_: self.fail('cross-tile corruption uploaded'), downloader=download)

    def test_second_pass_must_match_first_inventory_and_pilot_stops_cleanly(self):
        plan, download = source(archive([('tiles/2/000/000/001.gph', tile())]))
        with tempfile.TemporaryDirectory() as root:
            tiles, headers = inventory(plan, root, download)
            bad, changed = source(archive([('tiles/2/000/000/001.gph', tile(tail=1))]))
            with self.assertRaisesRegex(ValueError, 'second pass'):
                migrate(bad, root, tiles, headers, lambda *_: self.fail('changed bytes uploaded'), downloader=changed)
            calls = []
            result = migrate(plan, root, tiles, headers, lambda *args: calls.append(True), limit=1, downloader=download)
            self.assertEqual(result['tiles'], 1)
            self.assertEqual(calls, [True])
            self.assertEqual(list(Path(root).iterdir()), [])


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class PublisherTests(unittest.TestCase):
    def test_put_reads_back_complete_bytes_and_reuses_verified_object(self):
        objects = {}
        writes = []
        class S3:
            def get_object(self, Bucket, Key):
                if Key not in objects: raise FakeClientError('NoSuchKey')
                return dict(Body=io.BytesIO(objects[Key]))
            def put_object(self, **kwargs):
                assert kwargs['IfNoneMatch'] == '*'
                objects[kwargs['Key']] = kwargs['Body'].read(); writes.append(kwargs['Key'])
        with tempfile.TemporaryDirectory() as root, patch.dict(sys.modules, {'botocore.exceptions': SimpleNamespace(ClientError=FakeClientError, BotoCoreError=RuntimeError)}):
            file = Path(root) / 'tile'; file.write_bytes(b'good')
            item = dict(size=4, sha256=hashlib.sha256(b'good').hexdigest())
            publisher = Publisher(S3(), 'bucket')
            publisher.put('immutable', file, item)
            publisher.put('immutable', file, item)
            self.assertEqual(writes, ['immutable'])
            self.assertEqual((publisher.uploaded, publisher.reused), (1, 1))
            objects['immutable'] = b'bad!'
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                publisher.put('immutable', file, item)
            self.assertEqual(writes, ['immutable'], 'existing corrupt bytes must not be silently overwritten')

    def test_concurrent_conditional_winner_is_verified_never_overwritten(self):
        for code in ('PreconditionFailed', 'ConditionalRequestConflict'):
            for winner in (b'good', b'evil'):
                objects = {}
                class S3:
                    def get_object(self, **kwargs):
                        if not objects: raise FakeClientError('NoSuchKey')
                        return dict(Body=io.BytesIO(objects['key']))
                    def put_object(self, **kwargs):
                        assert kwargs['IfNoneMatch'] == '*'
                        objects['key'] = winner
                        raise FakeClientError(code)
                with tempfile.TemporaryDirectory() as root, patch.dict(sys.modules, {'botocore.exceptions': SimpleNamespace(ClientError=FakeClientError, BotoCoreError=RuntimeError)}):
                    file = Path(root) / 'tile'; file.write_bytes(b'good')
                    item = dict(size=4, sha256=hashlib.sha256(b'good').hexdigest())
                    publisher = Publisher(S3(), 'bucket')
                    if winner == b'good':
                        publisher.put('key', file, item)
                        self.assertEqual((publisher.uploaded, publisher.reused), (0, 1))
                    else:
                        with self.assertRaisesRegex(ValueError, 'SHA256'):
                            publisher.put('key', file, item)
                    self.assertEqual(objects['key'], winner)

    def test_access_denied_is_not_treated_as_missing(self):
        class S3:
            def get_object(self, **kwargs): raise FakeClientError('AccessDenied')
        with patch.dict(sys.modules, {'botocore.exceptions': SimpleNamespace(ClientError=FakeClientError, BotoCoreError=RuntimeError)}):
            with self.assertRaises(OSError):
                Publisher(S3(), 'bucket').verify('key', dict(size=1, sha256='a'*64))


if __name__ == '__main__':
    unittest.main()
