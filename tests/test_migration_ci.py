import hashlib
import io
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import migration_ci as ci


class MigrationCiTests(unittest.TestCase):
    def test_prepare_reads_every_asset_page_before_supply_gate(self):
        tag = 'tiles-fixture'
        marker = dict(name='READY', browser_download_url='https://github.com/WILLJBS/anipals-tiles/releases/download/'+tag+'/READY')
        first = [dict(name='asset-'+str(i)) for i in range(100)]
        calls = []
        def fetch(url, token=None, **kwargs):
            calls.append(url)
            if '/releases/tags/' in url: return json.dumps(dict(id=7, tag_name=tag)).encode()
            if url.endswith('&page=1'): return json.dumps(first).encode()
            if url.endswith('&page=2'): return json.dumps([marker]).encode()
            return b'ok\n'
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, 'fetch', side_effect=fetch), \
                patch('migration_contract.validate_supply') as gate, patch('migration_contract.build_plans', return_value=[dict(slug='region-'+str(i), parts=[]) for i in range(61)]):
            self.assertEqual(len(ci.prepare(tag, Path(folder))), 61)
            self.assertEqual(len(gate.call_args.args[0]['assets']), 101)
            self.assertTrue(any('page=2' in url for url in calls))
            self.assertEqual(len(json.loads((Path(folder)/'release.json').read_text())['assets']), 101)

    def test_authenticated_redirect_never_forwards_header(self):
        seen=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header('Location','http://127.0.0.1:%d/outside-api' % self.server.server_port)
                self.end_headers()
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(ci,'API','http://127.0.0.1:%d/api' % server.server_port):
                with self.assertRaisesRegex(ValueError,'redirect refused'):ci.fetch(ci.API+'/redirect','dummy-test-token')
            self.assertEqual(seen,['/api/redirect'])
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_refuses_token_forwarding_and_receipt_path_escape(self):
        with self.assertRaisesRegex(ValueError, 'token cannot'):
            ci.fetch('https://example.org/redirect', 'dummy-test-token')
        for tag, name, digest in [('tiles-x','../escape','a'*64),('../escape','pilot','a'*64),('tiles-x','pilot','x'*64)]:
            with self.assertRaises(ValueError):ci.receipt_key('b'*40,tag,name,digest)

    def test_source_sha_requires_exact_actual_checkout(self):
        with patch.object(ci.subprocess, 'check_output', return_value='a'*40+'\n'):
            ci.source_revision('a'*40)
            with self.assertRaisesRegex(ValueError, 'checkout differs'):ci.source_revision('b'*40)

    def test_pilot_readback_rejects_corruption_incomplete_count_and_other_bucket(self):
        try:import botocore
        except ImportError:self.skipTest('boto3 dependency installed by migration CI')
        class S3:
            def get_object(self, **kwargs):return dict(Body=io.BytesIO(self.raw))
        s3=S3()
        base=dict(schema=1,bucket='test-bucket',verified_tiles=20,manifest_published=False,contract='a'*64)
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, 'connection', return_value=(s3,'test-bucket')):
            output=Path(folder)/'pilot.json'
            for fault in (None,'bytes','count','bucket'):
                value=dict(base)
                if fault=='count':value['verified_tiles']=19
                if fault=='bucket':value['bucket']='other-bucket'
                raw=json.dumps(value).encode();sha=hashlib.sha256(raw).hexdigest()
                s3.raw=raw+b'x' if fault=='bytes' else raw
                if fault is None:
                    ci.read_pilot('b'*40,'tiles-test',sha,len(raw),output)
                    self.assertEqual(output.read_bytes(),raw);output.unlink()
                else:
                    with self.subTest(fault=fault),self.assertRaises(ValueError):
                        ci.read_pilot('b'*40,'tiles-test',sha,len(raw),output)
                    self.assertFalse(output.exists())


if __name__ == '__main__':unittest.main()
