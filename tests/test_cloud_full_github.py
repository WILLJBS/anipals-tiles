import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from cloud_full_github import actual_proof, API, request
import test_cloud_full_contract as fixtures


class ActualGithubProofTest(unittest.TestCase):
    setUp = fixtures.FullContractTest.setUp
    blob = fixtures.FullContractTest.blob
    proof = fixtures.FullContractTest.proof
    prepare = fixtures.FullContractTest.prepare
    def test_actual_api_run_and_job_log_replace_untrusted_uploaded_proof(self):
        ref = self.proof()['result']; calls = []
        def fetch(url, token=None, **kwargs):
            calls.append((url,token))
            if '/attempts/1/jobs?' in url:
                return json.dumps({'total_count':1,'jobs':[{'id':9,'run_id':1,'head_sha':'c'*40,'name':'pilot','conclusion':'success'}]}).encode()
            if url.endswith('/attempts/1'): return json.dumps(self.run).encode()
            if url.endswith('/jobs/9/logs'): return 'https://logs.blob.core.windows.net/private?signature=fixture'
            return json.dumps({'complete':True,'privateResult':ref}).encode()
        with tempfile.TemporaryDirectory() as tmp, patch('cloud_full_github.request',fetch), patch.dict('os.environ',{'GH_TOKEN':'fixture'}):
            proof=actual_proof(self.store,ref,Path(tmp)); contract=self.prepare([proof])
        self.assertEqual(contract['priorSourceBytes'],32)
        self.assertEqual(calls[-1][1],None)
        self.assertTrue(all(token=='fixture' for _,token in calls[:-1]))
    def test_token_cannot_go_to_redirect_or_other_repository(self):
        with self.assertRaisesRegex(ValueError,'DESTINATION'):
            request('https://example.invalid/log','fixture')
    def test_wrong_actual_attempt_fails_before_archiving(self):
        ref=self.proof()['result']
        with tempfile.TemporaryDirectory() as tmp, patch('cloud_full_github.request',return_value=json.dumps(self.run|{'run_attempt':2}).encode()):
            with self.assertRaisesRegex(ValueError,'ATTEMPT_RESPONSE'):
                actual_proof(self.store,ref,Path(tmp))


if __name__=='__main__': unittest.main()
