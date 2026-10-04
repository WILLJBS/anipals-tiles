import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from cloud_full_contract import prepare_contract, assert_lineage_current
from cloud_collector_io import Journal, PrivateStore
from cloud_execution import REPOSITORY, WORKFLOW
from test_cloud_collector import S3, upload


class HeadS3(S3):
    def head_object(self, Bucket, Key): return {'ContentLength': len(self.objects[Key])}


class FullContractTest(unittest.TestCase):
    def blob(self, value, prefix=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
        sha = hashlib.sha256(raw).hexdigest(); key = (prefix+sha+'.json') if prefix else f'archive/sha256/{sha[:2]}/{sha}'
        self.s3.objects[key] = raw; return {'key': key, 'sha256': sha, 'bytes': len(raw)}
    def setUp(self):
        self.s3 = HeadS3(); self.store = PrivateStore(self.s3, 'fixture-only', upload)
        empty = self.blob({})
        self.seed = {'schema': 'anipals-global-collector-seed-v1', 'partial': True, 'globalComplete': False,
                     'sourceScopeCount': 6222, 'roster': self.blob({'cities': 'test-only'}), 'index': self.blob(b'index'),
                     'completedSourceFiles': [], 'ranges': [], 'transferLedger': self.blob([{'body_bytes': 10}]),
                     'diagnosticLedger': self.blob({'results': [{'bodyBytes': 2}]}),
                     'rangeBudget': self.blob({'threeAttemptNetworkBudget': 1000}), 'progress': empty, 'summary': empty,
                     'sourceBudgetBytes': 1000, 'localSourceBytes': 10, 'diagnosticSourceBytes': 2}
        self.seed_ref = self.blob(self.seed)
        ds = [self.seed_ref]+[self.seed[k] for k in ('transferLedger', 'diagnosticLedger', 'rangeBudget', 'progress', 'summary')]
        self.receipt = self.blob({'schema': 'anipals-private-archive-v1', 'complete': True,
                                  'entries': [d | {'verified': True} for d in ds]})
        self.spec = {'schema': 'anipals-cloud-collector-v1', 'mode': 'pilot', 'roster': self.seed['roster'],
                     'index': self.seed['index'], 'priorSourceBytes': 0, 'publicationsAllowed': False,
                     'sourceBudgetBytes': 200, 'selectedNames': ['Test scope'], 'selectedGeoNamesIds': ['123'],
                     'appSourceSha': 'b'*40, 'code': self.blob(b'fixture-code')}
        self.namespace = self.blob(self.spec)['sha256']; self.journal = Journal(self.store, self.namespace, 200)
        token = self.journal.reserve(60); self.journal.settle(token, 20)
        self.execution = {'kind': 'github-actions', 'repository': REPOSITORY, 'workflowPath': WORKFLOW,
                          'runId': 1, 'runAttempt': 1, 'runnerSourceSha': 'c'*40, 'workflowSha': 'c'*40}
        self.result = {'schema': 'anipals-cloud-collector-result-v1', 'complete': True, 'specSha256': self.namespace,
                       'execution': self.execution, 'publication': 'pending', 'navigationTargetsPublished': 0,
                       'selectedNames': self.spec['selectedNames'], 'selectedGeoNamesIds': self.spec['selectedGeoNamesIds'],
                       'appSourceSha': self.spec['appSourceSha'], 'roster': self.seed['roster'], 'index': self.seed['index']}
        self.run = {'id': 1, 'run_attempt': 1, 'head_sha': 'c'*40, 'status': 'completed', 'conclusion': 'success',
                    'event': 'workflow_dispatch', 'path': WORKFLOW, 'repository': {'full_name': REPOSITORY}}
    def proof(self, result=None, run=None, wrong_log=False):
        ref = self.blob(result or self.result, f'archive/collector/{self.namespace}/results/')
        log = json.dumps({'complete': True, 'privateResult': (ref | {'sha256': '0'*64}) if wrong_log else ref})
        return {'result': ref, 'githubRun': self.blob(run or self.run), 'githubLog': self.blob(('timestamp '+log+'\n').encode())}
    def prepare(self, proofs=None):
        return prepare_contract(self.store, self.seed_ref, self.receipt, [self.proof()] if proofs is None else proofs)

    def test_actual_cloud_proof_is_required_not_zero_default(self):
        with self.assertRaisesRegex(ValueError, 'PILOT_PROOF_REQUIRED'): self.prepare([])
    def test_global_budget_includes_unsettled_and_deduplicates_namespace_receipts(self):
        self.journal.reserve(30); proof = self.proof(); result = self.prepare([proof, proof])
        self.assertEqual(result['priorSourceBytes'], 10+2+20+30)
        self.assertEqual(result['namespaceLedgers'][0]['unsettledUpperBytes'], 30)
        self.assertEqual(result['remainingSourceBytes'], 938)
    def test_unproved_failed_pilot_is_still_charged(self):
        second = self.spec | {'selectedNames': ['Other scope']}; namespace = self.blob(second)['sha256']
        Journal(self.store, namespace, 200).reserve(40)
        self.assertEqual(self.prepare()['priorSourceBytes'], 10+2+20+40)
    def test_changed_journal_rejects_stale_full_contract(self):
        result = self.prepare(); assert_lineage_current(self.store, self.seed, result)
        self.journal.reserve(30)
        with self.assertRaisesRegex(ValueError, 'LINEAGE_CHANGED'):
            assert_lineage_current(self.store, self.seed, result)
    def test_self_claimed_or_failed_cloud_or_wrong_log_is_rejected(self):
        bad = copy.deepcopy(self.result); bad['execution'] = {'kind': 'offline'}
        with self.assertRaisesRegex(ValueError, 'REAL_COMPLETE'): self.prepare([self.proof(result=bad)])
        with self.assertRaisesRegex(ValueError, 'GITHUB_SUCCESS'):
            self.prepare([self.proof(run=self.run | {'conclusion': 'failure'})])
        with self.assertRaisesRegex(ValueError, 'LOG_RESULT'):
            self.prepare([self.proof(wrong_log=True)])
    def test_partial_seed_archive_cannot_be_mistaken_for_verified(self):
        self.receipt = self.blob({'schema': 'anipals-private-archive-stage-v1', 'complete': False})
        with self.assertRaisesRegex(ValueError, 'SEED_ARCHIVE'): self.prepare()
    def test_full_namespace_only_adds_new_spend_not_inherited_cost_again(self):
        spec = self.spec | {'mode': 'full', 'priorSourceBytes': 32, 'sourceBudgetBytes': 1000}
        namespace = self.blob(spec)['sha256']; j = Journal(self.store, namespace, 1000, 32)
        token = j.reserve(15); j.settle(token, 5)
        self.assertEqual(self.prepare()['priorSourceBytes'], 10+2+20+5)
    def test_missing_prior_and_ambiguous_roster_fail_closed(self):
        bad = self.spec | {'selectedNames': ['Missing prior']}; del bad['priorSourceBytes']
        namespace = self.blob(bad)['sha256']; Journal(self.store, namespace, 200).reserve(1)
        with self.assertRaisesRegex(ValueError, 'PRIOR_CHARGE'): self.prepare()


if __name__ == '__main__': unittest.main()
