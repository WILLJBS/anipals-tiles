import hashlib
import io
import json
from urllib.error import HTTPError
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from display_archive import transfer, read_range, SOURCE, USER_AGENT, source_preflight, failure_details
class Missing(Exception):
    response = {'Error': {'Code': '404'}}


class Store:
    def __init__(self, corrupt=False):
        self.created = False;self.parts = [];self.completed = False;self.aborted = False;self.corrupt = corrupt
    def head_object(self, **args):
        if not self.parts: raise Missing()
        return {'ContentLength': len(b''.join(self.parts)),
                'Metadata': self.metadata, 'ETag': '"actual-etag-2"'}
    def create_multipart_upload(self, **args):
        self.created = True;self.metadata = args['Metadata'];return {'UploadId': 'fixture'}
    def upload_part(self, **args):
        self.parts.append(args['Body']);return {'ETag': str(args['PartNumber'])}
    def complete_multipart_upload(self, **args):
        assert args['IfNoneMatch'] == '*';self.completed = True
    def abort_multipart_upload(self, **args):
        self.aborted = True
    def get_object(self, **args):
        raw = b'bad' if self.corrupt else b''.join(self.parts)
        start, end = map(int, args['Range'].removeprefix('bytes=').split('-'))
        assert args['IfMatch'] == '"actual-etag-2"'
        return {'Body': io.BytesIO(raw[start:end+1]), 'ContentLength': end-start+1,
                'ContentRange': f'bytes {start}-{end}/{len(raw)}',
                'ResponseMetadata': {'HTTPStatusCode': 206},
                'Metadata': self.metadata, 'ETag': '"actual-etag-2"'}


class Streaming(unittest.TestCase):
    def test_identified_source_client_retains_strict_range_headers(self):
        class Response(io.BytesIO):
            status = 206
            headers = {'Content-Range': 'bytes 0-2/3'}
            def geturl(self): return SOURCE
        def opener(request, **kwargs):
            self.assertEqual(request.get_header('User-agent'), USER_AGENT)
            self.assertEqual(request.get_header('Range'), 'bytes=0-2')
            self.assertEqual(request.get_header('Accept-encoding'), 'identity')
            return Response(b'abc')
        self.assertEqual(read_range(0, 2, 3, opener=opener), b'abc')

    def test_actual_http_error_is_redacted_with_stage_status_and_bounded_retries(self):
        calls, waits, bodies = [], [], []
        def denied(*args, **kwargs):
            calls.append(1); body = io.BytesIO(b'private upstream body'); bodies.append(body)
            raise HTTPError('https://private.invalid/?token=secret', 403, 'secret response', {}, body)
        with self.assertRaises(HTTPError) as failure:
            read_range(0, 2, 3, opener=denied, sleep=waits.append)
        details = failure_details(failure.exception)
        self.assertEqual((len(calls), waits), (3, [2, 2]))
        self.assertTrue(all(body.closed for body in bodies))
        self.assertEqual(details['stage'], 'source_range'); self.assertEqual(details['http_status'], 403)
        self.assertNotIn('secret', json.dumps(details)); self.assertNotIn('private', json.dumps(details))
        self.assertEqual(failure_details(ValueError('private token'))['reason'], 'BASEMAP_TRANSFER_FAILED')

    def test_preflight_validates_header_before_any_multipart_creation(self):
        for body in (b'bad', b'PMTiles\x02'+b'0'*119):
            store=Store()
            with self.assertRaisesRegex(ValueError, 'SOURCE_PMTILES_HEADER'):
                transfer(store, 'fixture', preflight=lambda: source_preflight(lambda *args: body))
            self.assertFalse(store.created); self.assertEqual(store.parts, [])
        body=b'PMTiles\x03'+b'0'*119
        proof=source_preflight(lambda *args: body)
        self.assertEqual(proof['rangeBytes'], 127)

    def test_preflight_network_failure_is_before_write_and_upload_failure_keeps_stage(self):
        store=Store()
        def denied(): raise HTTPError('https://private.invalid', 403, 'private', {}, None)
        with self.assertRaises(HTTPError) as failure: transfer(store, 'fixture', preflight=denied)
        self.assertFalse(store.created); self.assertEqual(failure_details(failure.exception)['stage'], 'source_preflight')
        def upload(**kwargs): raise RuntimeError('private token')
        store.upload_part=upload
        with self.assertRaises(RuntimeError) as failure:
            transfer(store, 'fixture', size=3, sha256='a'*64, reader=lambda *args:b'abc')
        self.assertTrue(store.aborted); self.assertEqual(failure_details(failure.exception)['stage'], 'r2_part_upload')

    def test_hash_before_complete_and_full_readback(self):
        raw = b'abcdef';store = Store()
        result = transfer(store, 'fixture', size=6, part_size=3, sha256=hashlib.sha256(raw).hexdigest(),
                          reader=lambda start,end,size: raw[start:end+1])
        self.assertTrue(store.completed);self.assertFalse(store.aborted)
        self.assertEqual(result['etag'], 'actual-etag-2')
        self.assertEqual(store.parts, [b'abc', b'def'])

    def test_bad_full_sha_aborts_without_publishing(self):
        store = Store()
        with self.assertRaisesRegex(ValueError, 'FULL_SOURCE_SHA'):
            transfer(store, 'fixture', size=3, part_size=3, sha256='0'*64, reader=lambda *args:b'abc')
        self.assertFalse(store.completed);self.assertTrue(store.aborted)

    def test_remote_corruption_never_returns_verified_receipt(self):
        store = Store(corrupt=True)
        with self.assertRaisesRegex(ValueError, 'REMOTE_SHA'):
            transfer(store, 'fixture', size=3, part_size=3, sha256=hashlib.sha256(b'abc').hexdigest(), reader=lambda *args:b'abc')
        self.assertTrue(store.completed)

    def test_http_200_wrong_range_and_truncation_cannot_be_parts(self):
        class Response(io.BytesIO):
            def geturl(self):return getattr(self, 'url', SOURCE)
        for status, headers, body in [(200, {'Content-Range':'bytes 0-2/3'},b'abc'),
          (206, {'Content-Range':'bytes 1-3/3'},b'abc'),(206, {'Content-Range':'bytes 0-2/3'},b'ab')]:
            attempts=[]
            def opener(*args, **kwargs):
                attempts.append(1);r=Response(body);r.status=status;r.headers=headers;return r
            with self.assertRaises(ValueError):read_range(0,2,3,opener=opener,sleep=lambda n:None)
            self.assertEqual(len(attempts),3)

    def test_different_final_source_is_not_accepted(self):
        class Redirected(io.BytesIO):
            status=206
            headers={'Content-Range':'bytes 0-2/3'}
            def geturl(self):return 'https://unregistered.example/other'
        with self.assertRaisesRegex(ValueError,'SOURCE_RANGE_IDENTITY'):
            read_range(0,2,3,opener=lambda *a,**k:Redirected(b'abc'),sleep=lambda n:None)

    def test_conditional_winner_with_wrong_bytes_is_rejected(self):
        store=Store(corrupt=True)
        class Conflict(Exception):response={'Error':{'Code':'PreconditionFailed'}}
        def conflict(**args):raise Conflict()
        store.complete_multipart_upload=conflict
        with self.assertRaisesRegex(ValueError,'REMOTE_SHA'):
            transfer(store,'fixture',size=3,part_size=3,sha256=hashlib.sha256(b'abc').hexdigest(),reader=lambda *args:b'abc')
        self.assertTrue(store.aborted)

    def test_conditional_completion_conflict_verifies_winner_and_aborts_own_parts(self):
        store=Store()
        class Conflict(Exception):response={'Error':{'Code':'PreconditionFailed'}}
        def conflict(**args):raise Conflict()
        store.complete_multipart_upload=conflict
        result=transfer(store,'fixture',size=3,part_size=3,sha256=hashlib.sha256(b'abc').hexdigest(),reader=lambda *args:b'abc')
        self.assertTrue(store.aborted);self.assertEqual(result['bytes'],3)

    def test_interruption_aborts_unfinished_upload(self):
        store=Store()
        def interrupted(*args):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            transfer(store,'fixture',size=3,sha256='a'*64,reader=interrupted)
        self.assertTrue(store.aborted);self.assertFalse(store.completed)

    def test_transfer_failure_keeps_primary_error_if_abort_also_fails(self):
        store=Store()
        def abort(**args):raise RuntimeError('abort failed')
        store.abort_multipart_upload=abort
        with self.assertRaisesRegex(ValueError, 'FULL_SOURCE_SHA') as failure:
            transfer(store,'fixture',size=3,sha256='0'*64,reader=lambda *args:b'abc')
        self.assertTrue(failure.exception.multipart_abort_failed)


if __name__ == '__main__':unittest.main()
