import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_r2 import reader


class R2Tests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.factory = Mock(return_value=self.client)
        self.config = Mock()
        self.modules = {
            'boto3': SimpleNamespace(client=self.factory),
            'botocore.config': SimpleNamespace(Config=self.config),
            'botocore.exceptions': SimpleNamespace(BotoCoreError=RuntimeError, ClientError=LookupError),
        }
        self.env = dict(R2_ENDPOINT_URL='https://example.r2.cloudflarestorage.com',
                        R2_BUCKET='test-bucket', R2_ACCESS_KEY_ID='test-key',
                        R2_SECRET_ACCESS_KEY='test-secret')

    def test_signed_private_stream_is_closed_on_early_consumer_stop(self):
        body = io.BytesIO(b'x' * (2 * 1024 * 1024))
        self.client.get_object.return_value = dict(Body=body)
        with patch.dict(sys.modules, self.modules):
            fetch = reader(self.env)
        chunks = fetch('navigation/graphs/a/b/tiles/0/001.gph')
        self.assertEqual(len(next(chunks)), 1024 * 1024)
        chunks.close()
        self.assertTrue(body.closed)
        self.config.assert_called_once_with(signature_version='s3v4', connect_timeout=2,
            read_timeout=3, retries={'total_max_attempts': 3, 'mode': 'standard'})
        self.assertEqual(self.factory.call_args.kwargs['region_name'], 'auto')
        self.assertEqual(self.client.get_object.call_args.kwargs['Bucket'], 'test-bucket')

    def test_transport_error_is_redacted_and_does_not_become_empty_object(self):
        self.client.get_object.side_effect = LookupError('private-token account data')
        with patch.dict(sys.modules, self.modules):
            fetch = reader(self.env)
        with self.assertRaisesRegex(OSError, '^R2 object retrieval failed$'):
            list(fetch('key'))

    def test_bad_endpoint_and_bucket_fail_before_constructing_client(self):
        endpoints = ['http://example.r2.cloudflarestorage.com',
                     'https://user:secret@example.r2.cloudflarestorage.com',
                     'https://example.r2.cloudflarestorage.com.evil.invalid',
                     'https://example.r2.cloudflarestorage.com/path',
                     'https://example.r2.cloudflarestorage.com?token=secret']
        with patch.dict(sys.modules, self.modules):
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    reader(dict(self.env, R2_ENDPOINT_URL=endpoint))
            with self.assertRaises(ValueError):
                reader(dict(self.env, R2_BUCKET='../bad'))
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
