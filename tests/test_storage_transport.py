import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
sys.path.insert(0, str(ROOT/'deploy'))
from storage_errors import error_details
from migration_transport import connection, bulk_config
from migration_r2 import Publisher
from private_archive_verify import publish_receipt, read_verified, archival_config
from display_archive import conflict, transfer

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError, ClientError
except ImportError:
    boto3 = None


class StorageErrorTests(unittest.TestCase):
    def test_unknown_and_malformed_metadata_never_implies_missing_or_conflict(self):
        for response in (None, [], {'Error': None}, {'Error': {'Code': []}},
                         {'Error': {'Code': 'private arbitrary service detail'}},
                         {'ResponseMetadata': None}, {'ResponseMetadata': {'HTTPStatusCode': '412'}}):
            error = RuntimeError('private message'); error.response = response
            self.assertEqual(error_details(error), (None, None))
            self.assertFalse(conflict(error))
        known = RuntimeError(); known.response = {'Error': {'Code': 'AccessDenied'}}
        self.assertEqual(error_details(known), ('AccessDenied', None))

    @unittest.skipUnless(boto3, 'real SDK required')
    def test_sdk_none_response_does_not_mask_or_swallow_transport_error(self):
        error = ReadTimeoutError(endpoint_url='https://example.invalid')
        self.assertIsNone(error.response)
        with self.assertRaises(AttributeError):
            getattr(error, 'response', {}).get('Error', {}).get('Code')
        self.assertEqual(error_details(error), (None, None))
        self.assertFalse(conflict(error))
        store = Mock(); store.get_object.side_effect = error
        with self.assertRaisesRegex(OSError, '^R2 verification read failed$'):
            Publisher(store, 'test-bucket').verify('key', {'size': 1, 'sha256': '0'*64})
        store.put_object.side_effect = error
        with patch('private_archive_verify.time.sleep'), self.assertRaises(ReadTimeoutError):
            publish_receipt(store, 'test-bucket', b'{}')
        self.assertEqual(store.put_object.call_count, 3)
        store.head_object.side_effect = error
        with self.assertRaises(ReadTimeoutError):
            transfer(store, 'test-bucket')
        store.create_multipart_upload.assert_not_called()

    @unittest.skipUnless(boto3, 'real SDK required')
    def test_connect_timeout_has_exactly_three_outer_attempts(self):
        error = ConnectTimeoutError(endpoint_url='https://example.invalid')
        for operation in ('read', 'write'):
            store = Mock(); store.get_object.side_effect = error; store.put_object.side_effect = error
            with patch('private_archive_verify.time.sleep') as sleep, self.assertRaises(ConnectTimeoutError):
                if operation == 'read': read_verified(store, 'test-bucket', 'key', '0'*64, 1)
                else: publish_receipt(store, 'test-bucket', b'{}')
            self.assertEqual((store.get_object if operation == 'read' else store.put_object).call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    @unittest.skipUnless(boto3, 'real SDK required')
    def test_malformed_client_error_fails_closed(self):
        error = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        error.response = None
        store = Mock(); store.get_object.side_effect = error
        with self.assertRaisesRegex(OSError, '^R2 verification read failed$'):
            Publisher(store, 'test-bucket').verify('key', {'size': 1, 'sha256': '0'*64})
        store.put_object.assert_not_called()


@unittest.skipUnless(boto3, 'real SDK required')
class BulkTransportTests(unittest.TestCase):
    def test_bulk_client_validates_destination_closes_initial_client_and_has_separate_budget(self):
        env = dict(R2_ENDPOINT_URL='https://example.r2.cloudflarestorage.com',
                   R2_BUCKET='test-bucket', R2_ACCESS_KEY_ID='dummy', R2_SECRET_ACCESS_KEY='dummy')
        with patch('boto3.client') as factory:
            validated, bulk = Mock(), Mock()
            validated.meta.endpoint_url = env['R2_ENDPOINT_URL']
            factory.side_effect = [validated, bulk]
            result, bucket = connection(env)
            self.assertIs(result, bulk); self.assertEqual(bucket, 'test-bucket')
            validated.close.assert_called_once()
            online = factory.call_args_list[0].kwargs['config']
            offline = factory.call_args_list[1].kwargs['config']
            self.assertEqual((online.connect_timeout, online.read_timeout), (2, 3))
            self.assertEqual((offline.connect_timeout, offline.read_timeout), (10, 90))
            self.assertEqual(offline.retries, {'total_max_attempts': 3, 'mode': 'standard'})
        with patch('boto3.client') as factory:
            with self.assertRaises(ValueError):
                connection(dict(env, R2_ENDPOINT_URL='https://example.invalid'))
            factory.assert_not_called()

    def test_archive_sdk_plus_outer_retry_is_exactly_three_http_requests(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def fail(self):
                requests.append(self.command)
                body = b'<Error><Code>ServiceUnavailable</Code></Error>'
                self.send_response(503); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            do_GET = fail
            do_PUT = fail
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        client = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{server.server_port}',
            region_name='auto', aws_access_key_id='dummy', aws_secret_access_key='dummy',
            config=archival_config().merge(Config(proxies={})))
        try:
            with patch('private_archive_verify.time.sleep') as sleep:
                with self.assertRaises(ClientError):
                    read_verified(client, 'test-bucket', 'key', '0'*64, 1)
                self.assertEqual(requests, ['GET']*3); self.assertEqual(sleep.call_count, 2)
            requests.clear()
            with patch('private_archive_verify.time.sleep') as sleep:
                with self.assertRaises(ClientError): publish_receipt(client, 'test-bucket', b'{}')
                self.assertEqual(requests, ['PUT']*3); self.assertEqual(sleep.call_count, 2)
            for status in (403, 404):
                store = Mock(); store.get_object.side_effect = ClientError(
                    {'Error': {'Code': str(status)}, 'ResponseMetadata': {'HTTPStatusCode': status}}, 'GetObject')
                with self.assertRaises(ClientError): read_verified(store, 'test-bucket', 'key', '0'*64, 1)
                self.assertEqual(store.get_object.call_count, 1)
        finally:
            client.close(); server.shutdown(); server.server_close(); worker.join()

    def test_real_delayed_http_sdk_read_fails_short_budget_succeeds_bulk(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                time.sleep(4)
                try:
                    self.send_response(200); self.send_header('Content-Length', '4')
                    self.end_headers(); self.wfile.write(b'tile')
                except (BrokenPipeError, ConnectionResetError): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        clients = []
        try:
            for budget, expect_timeout in ((Config(connect_timeout=2, read_timeout=3), True),
                                           (bulk_config(), False)):
                config = budget.merge(Config(retries={'total_max_attempts': 1}, proxies={}))
                client = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{server.server_port}',
                    region_name='auto', aws_access_key_id='dummy', aws_secret_access_key='dummy', config=config)
                clients.append(client)
                if expect_timeout:
                    with self.assertRaises(ReadTimeoutError):
                        client.get_object(Bucket='test-bucket', Key='key')
                else:
                    body = client.get_object(Bucket='test-bucket', Key='key')['Body']
                    try: self.assertEqual(body.read(), b'tile')
                    finally: body.close()
        finally:
            for client in clients: client.close()
            server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__': unittest.main()
