import hashlib
import io
from pathlib import Path
import sys
import unittest
from botocore.exceptions import ClientError, ResponseStreamingError
from botocore.response import StreamingBody
from urllib3.exceptions import ProtocolError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from display_archive import transfer, failure_details
from display_readback import verify_remote, RANGE_BYTES, READ_BYTES
from private_archive_verify import transient


class Store:
    def __init__(self, raw=b'abcdefg'):
        self.raw = raw
        self.sha = hashlib.sha256(raw).hexdigest()
        self.etag = '"fixture-multipart-2"'
        self.gets, self.heads, self.bodies = [], [], []
        self.mutate = lambda response, args: response

    def head_object(self, **args):
        self.heads.append(args)
        return {'ContentLength': len(self.raw), 'ETag': self.etag,
                'Metadata': {'sha256': self.sha}}

    def get_object(self, **args):
        self.gets.append(args)
        start, end = map(int, args['Range'][6:].split('-'))
        response = {'ContentLength': end-start+1, 'ETag': self.etag,
                    'ContentRange': f'bytes {start}-{end}/{len(self.raw)}',
                    'Metadata': {'sha256': self.sha},
                    'ResponseMetadata': {'HTTPStatusCode': 206},
                    'Body': io.BytesIO(self.raw[start:end+1])}
        response = self.mutate(response, args)
        self.bodies.append(response['Body'])
        return response


class BrokenStream(io.BytesIO):
    """One actual SDK streaming read returns bytes, then its transport breaks."""
    def read(self, count=-1):
        if self.tell():
            raise ProtocolError('private transport details')
        return super().read(1)


class Readback(unittest.TestCase):
    def verify(self, store, **kwargs):
        return verify_remote(store, 'fixture', 'basemap.pmtiles', len(store.raw), store.sha,
                             range_bytes=3, **kwargs)

    def test_ordered_full_sha_and_partial_final_range_with_conditional_identity(self):
        store, events = Store(), []
        result = self.verify(store, progress=events.append)
        self.assertEqual(result, {'key': 'basemap.pmtiles', 'bytes': 7,
                                'sha256': store.sha, 'etag': 'fixture-multipart-2'})
        self.assertEqual([r['Range'] for r in store.gets], ['bytes=0-2', 'bytes=3-5', 'bytes=6-6'])
        self.assertTrue(all(r['IfMatch'] == store.etag for r in store.gets))
        self.assertEqual(store.heads[-1]['IfMatch'], store.etag)
        self.assertTrue(all(b.closed for b in store.bodies))
        self.assertEqual([e['verifiedBytes'] for e in events], [0, 3, 6, 7])
        self.assertTrue(all(e['complete'] is False for e in events))

    def test_actual_sdk_stream_error_retries_only_failed_range_without_double_hash(self):
        store, events, waits, raw_streams = Store(), [], [], []
        def broken(response, args):
            if len(store.gets) == 2:
                response['Body'].close()
                raw = BrokenStream(b'def'); raw_streams.append(raw)
                response['Body'] = StreamingBody(raw, 3)
            return response
        store.mutate = broken
        self.verify(store, progress=events.append, sleep=waits.append)
        self.assertEqual([r['Range'] for r in store.gets],
                         ['bytes=0-2', 'bytes=3-5', 'bytes=3-5', 'bytes=6-6'])
        self.assertEqual(waits, [2]); self.assertTrue(raw_streams[0].closed)
        retries = [e for e in events if e['event'] == 'r2_readback_retry']
        self.assertEqual([(e['verifiedBytes'], e['nextAttempt']) for e in retries], [(3, 2)])
        self.assertTrue(transient(ResponseStreamingError(error='fixture')))

    def test_premature_eof_is_retried_without_committing_partial_hash(self):
        store, waits = Store(), []
        def truncated(response, args):
            if len(store.gets) == 1:
                response['Body'].close(); response['Body'] = io.BytesIO(b'ab')
            return response
        store.mutate = truncated
        self.verify(store, sleep=waits.append)
        self.assertEqual(waits, [2]); self.assertEqual(len(store.gets), 4)

    def test_stream_retries_exhaust_with_no_complete_receipt_and_closed_bodies(self):
        store, events, waits, raw_streams = Store(), [], [], []
        def broken(response, args):
            response['Body'].close()
            raw = BrokenStream(b'abc'); raw_streams.append(raw)
            response['Body'] = StreamingBody(raw, 3)
            return response
        store.mutate = broken
        with self.assertRaises(ResponseStreamingError) as failure:
            self.verify(store, progress=events.append, sleep=waits.append)
        self.assertEqual(len(store.gets), 3); self.assertEqual(waits, [2, 2])
        self.assertTrue(all(raw.closed for raw in raw_streams))
        self.assertTrue(all(e['verifiedBytes'] == 0 and not e['complete'] for e in events))
        self.assertNotIn('private', str(failure_details(failure.exception)))

    def test_bad_response_identity_is_fail_closed_without_retry(self):
        changes = [{'ETag': '"replacement"'}, {'ETag': ''}, {'ETag': 'W/"weak"'},
                   {'ContentLength': 4}, {'Metadata': {'sha256': '0'*64}},
                   {'ContentEncoding': 'gzip'}, {'ContentRange': 'bytes 1-3/7'},
                   {'ContentRange': 'bytes 0-2/8'}, {'ResponseMetadata': {'HTTPStatusCode': 200}},
                   {'ContentRange': None}]
        for changeset in changes:
            with self.subTest(changes=changeset):
                store, waits = Store(), []
                store.mutate = lambda response, args: response | changeset
                with self.assertRaises(ValueError): self.verify(store, sleep=waits.append)
                self.assertEqual(len(store.gets), 1); self.assertEqual(waits, [])
                self.assertTrue(store.bodies[0].closed)

    def test_precondition_failure_is_not_retried(self):
        store, waits, calls = Store(), [], []
        def replaced(**args):
            calls.append(args)
            raise ClientError({'Error': {'Code': 'PreconditionFailed'},
                               'ResponseMetadata': {'HTTPStatusCode': 412}}, 'GetObject')
        store.get_object = replaced
        with self.assertRaises(ClientError): self.verify(store, sleep=waits.append)
        self.assertEqual(len(calls), 1); self.assertEqual(waits, [])

    def test_same_size_corruption_and_long_body_never_pass_sha(self):
        for data, reason in [(b'xyz', 'REMOTE_SHA'), (b'abcd', 'REMOTE_SIZE')]:
            store = Store()
            def corrupt(response, args):
                if len(store.gets) == 1:
                    response['Body'].close(); response['Body'] = io.BytesIO(data)
                return response
            store.mutate = corrupt
            with self.assertRaisesRegex(ValueError, reason): self.verify(store)
            self.assertTrue(all(b.closed for b in store.bodies))

    def test_final_head_detects_metadata_or_identity_drift(self):
        for changes in [{'ETag': '"replacement"'}, {'Metadata': {'sha256': '0'*64}},
                        {'ContentLength': 8}]:
            store = Store(); original = store.head_object
            def head(**args):
                response = original(**args)
                return response | changes if 'IfMatch' in args else response
            store.head_object = head
            with self.assertRaises(ValueError): self.verify(store)
            self.assertEqual(len(store.gets), 3)

    def test_head_transport_retry_and_initial_metadata_rejection(self):
        store, waits = Store(), []
        original = store.head_object; failures = [True]
        def head(**args):
            if failures:
                failures.pop(); raise TimeoutError('private details')
            return original(**args)
        store.head_object = head
        self.verify(store, sleep=waits.append); self.assertEqual(waits, [2])
        store = Store()
        store.head_object = lambda **args: {'ContentLength': 7, 'ETag': store.etag}
        with self.assertRaisesRegex(ValueError, 'REMOTE_METADATA'): self.verify(store)
        self.assertEqual(store.gets, [])

    def test_existing_completed_object_reuses_readback_without_source_or_upload(self):
        store = Store()
        def forbidden(*args, **kwargs): raise AssertionError('source/upload must not run')
        store.create_multipart_upload = forbidden
        result = transfer(store, 'fixture', size=7, sha256=store.sha, reader=forbidden, preflight=forbidden)
        self.assertEqual(result['sha256'], store.sha)
        self.assertEqual(len(store.gets), 1)

    def test_large_range_is_streamed_in_bounded_chunks(self):
        store = Store(b'x' * (READ_BYTES + 7)); reads = []
        class Measured(io.BytesIO):
            def read(self, count=-1):
                reads.append(count)
                return super().read(count)
        def measured(response, args):
            response['Body'].close(); response['Body'] = Measured(store.raw)
            return response
        store.mutate = measured
        verify_remote(store, 'fixture', 'basemap.pmtiles', len(store.raw), store.sha)
        self.assertEqual(reads, [READ_BYTES, 8, 1])
        self.assertTrue(store.bodies[0].closed)

    def test_readback_interruption_closes_body_without_retry_or_complete_event(self):
        store, events = Store(), []
        class Interrupted(io.BytesIO):
            def read(self, count=-1): raise KeyboardInterrupt()
        def interrupted(response, args):
            response['Body'].close(); response['Body'] = Interrupted(b'abc')
            return response
        store.mutate = interrupted
        with self.assertRaises(KeyboardInterrupt): self.verify(store, progress=events.append)
        self.assertEqual(len(store.gets), 1); self.assertTrue(store.bodies[0].closed)
        self.assertTrue(all(e['verifiedBytes'] == 0 and not e['complete'] for e in events))

    def test_invalid_identity_or_unbounded_range_never_reads(self):
        for changes in [{'size': 0}, {'size': True}, {'sha256': 'no'},
                        {'range_bytes': RANGE_BYTES+1}, {'range_bytes': 0}, {'range_bytes': True}]:
            store = Store()
            args = {'size': 7, 'sha256': store.sha, 'range_bytes': 3} | changes
            with self.assertRaisesRegex(ValueError, 'INVALID_READBACK_IDENTITY'):
                verify_remote(store, 'fixture', 'basemap.pmtiles', **args)
            self.assertEqual(store.heads, []); self.assertEqual(store.gets, [])


if __name__ == '__main__': unittest.main()
